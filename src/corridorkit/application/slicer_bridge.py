"""Headless bridge for Slicer exports; no Slicer, Qt, or atlas dependencies.

All ``.npy`` masks must be binary x,y,z arrays on the *same full CT grid*.
``affine`` maps their voxel centers to world RAS millimetres, including any
hardened parent transform. Entries and target_point are in that same world
frame. Arrays have no spatial metadata: common-grid and mm units are exporter
declarations, not something this bridge can independently establish. Per-mask
affines/transforms, cropped grids, LPS coordinates, and z,y,x arrays are not
accepted by this contract; convert/resample in Slicer before exporting.

Public API: ``build_case(request, base_directory=...) -> CorridorCase`` and
``run_request(request_path, output_dir) -> dict``. CLI:
``python -m corridorkit.application.slicer_bridge --request request.json
--output-dir output``. Relative mask paths resolve against the request file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from pydantic import Field, StrictBool, StringConstraints, field_validator, model_validator

from corridorkit.analysis.coverage import (
    SAMPLING_CAVEAT,
    build_coverage_comparison,
    export_comparison,
)
from corridorkit.domain.models import (
    ApproachConfig,
    ApproachKind,
    CorridorCase,
    ExactPathCandidate,
    KnowledgeStatus,
    Mat4,
    PortalDisk,
    ProtectedStructure,
    RigidInstrument,
    SamplingConfig,
    StrictModel,
    Vec3,
    VirtualBoneRemoval,
    VoxelGeometry,
    validate_physical_affine,
)
from corridorkit.export.json import (
    atomic_json_write,
    export_result,
    file_sha256,
    write_case,
)
from corridorkit.geometry.engine import analyze_case, evaluate_exact_paths
from corridorkit.io.masks import target_from_mask

BRIDGE_SCHEMA_VERSION = "1.0"
BONE_NAME = "__slicer_bone__"
UNKNOWN_NAME = "__undeclared_critical_anatomy__"
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class BridgeSampling(SamplingConfig):
    """Bounded finite sampling, conservative collisions, no expensive refinement."""

    polar_steps: int = Field(default=2, ge=1, le=9, strict=True)
    azimuth_steps: int = Field(default=12, ge=1, le=48, strict=True)
    target_directed: StrictBool = True
    adaptive_levels: int = Field(default=0, ge=0, le=1, strict=True)
    max_target_witnesses: int = Field(default=32, ge=1, le=128, strict=True)
    max_refinement_directions: int = Field(default=0, ge=0, le=64, strict=True)
    portal_offset_rings: int = Field(default=0, ge=0, le=1, strict=True)
    portal_offset_azimuth_steps: int = Field(default=8, ge=1, le=8, strict=True)


class ProtectedMask(StrictModel):
    name: Name
    path: Name


class SlicerBridgeRequest(StrictModel):
    """Versioned JSON contract. Unknown keys fail closed, including transforms."""

    schema_version: Literal["1.0"] = BRIDGE_SCHEMA_VERSION
    case_id: Name = "slicer-export"
    coordinate_frame: Literal["RAS"] = "RAS"
    units: Literal["mm"] = "mm"
    array_order: Literal["xyz"] = "xyz"
    target: Name
    bone: Name | None = None
    protected: list[ProtectedMask] = Field(default_factory=list)
    removals: dict[Literal["EEA", "CTM"], Name] = Field(default_factory=dict)
    affine: Mat4
    entries: dict[Literal["EEA", "CTM"], list[float]]
    target_point: Vec3
    shaft_diameter_mm: float = Field(gt=0)
    portal_diameter_mm: float = Field(gt=0)
    instrument_length_mm: float = Field(gt=0)
    anatomy_complete: StrictBool = False
    removals_reviewed: StrictBool = False
    reviewer: str = ""
    sampling: BridgeSampling = Field(default_factory=BridgeSampling)
    max_target_count: int = Field(default=2000, ge=1, le=2000, strict=True)
    exact_candidates: list[ExactPathCandidate] = Field(default_factory=list)

    @field_validator("affine")
    @classmethod
    def physical_affine(cls, value):
        return validate_physical_affine(value)

    @field_validator("reviewer")
    @classmethod
    def trimmed_reviewer(cls, value):
        return value.strip()

    @model_validator(mode="after")
    def validate_contract(self):
        if not any(self.entries.values()):
            raise ValueError("entries must supply at least one EEA or CTM world RAS point")
        for name, point in self.entries.items():
            if len(point) not in (0, 3):
                raise ValueError(f"entries.{name} must contain three RAS mm coordinates or []")
            if point and np.linalg.norm(np.asarray(point) - self.target_point) <= 1e-9:
                raise ValueError(f"entries.{name} must differ from target_point")
        names = [item.name for item in self.protected]
        if len(set(names)) != len(names) or set(names) & {BONE_NAME, UNKNOWN_NAME}:
            raise ValueError("protected names must be unique and not bridge-reserved names")
        if self.removals:
            if self.bone is None or not self.removals_reviewed or not self.reviewer:
                raise ValueError("removals require bone, removals_reviewed=true, and a reviewer")
            if any(not self.entries.get(name) for name in self.removals):
                raise ValueError("each removal must have a configured entry for that approach")
        candidate_ids = [candidate.candidate_id for candidate in self.exact_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("exact candidate IDs must be unique")
        return self

    @property
    def completeness_declared(self) -> bool:
        return bool(self.anatomy_complete and self.reviewer and self.protected)


def _request(value: SlicerBridgeRequest | dict) -> SlicerBridgeRequest:
    # Revalidate model_copy/model_construct inputs as well as raw JSON.
    return SlicerBridgeRequest.model_validate(
        value.model_dump() if isinstance(value, SlicerBridgeRequest) else value
    )


def _mask_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    path = (base / path).resolve() if not path.is_absolute() else path.resolve()
    if path.suffix != ".npy":
        raise ValueError(f"mask must be an exported .npy array: {value}")
    return path


def _load_binary(path: Path, role: str, shape: tuple | None = None) -> np.ndarray:
    data = np.load(path, allow_pickle=False, mmap_mode="r")
    if data.ndim != 3 or any(size == 0 for size in data.shape):
        raise ValueError(f"{role}: mask must be a nonempty three-dimensional xyz array")
    if shape is not None and data.shape != shape:
        raise ValueError(
            f"{role}: shape {data.shape} differs from target full CT grid {shape}; "
            "re-export every mask on the same full reference CT grid, without cropping"
        )
    if data.dtype.kind not in "buif" or not np.all((data == 0) | (data == 1)):
        raise ValueError(f"{role}: mask must be finite and binary (0/1), not a label map")
    return data


def build_case(
    request: SlicerBridgeRequest | dict, *, base_directory: str | Path | None = None,
) -> CorridorCase:
    """Validate all exports and construct existing engine models without analysis."""
    request = _request(request)
    base = Path(base_directory or Path.cwd()).resolve()
    target_path = _mask_path(request.target, base)
    target_mask = _load_binary(target_path, "target")
    count = int(np.count_nonzero(target_mask))
    if count == 0:
        raise ValueError("target: mask has no foreground voxels")
    if count > request.max_target_count:
        raise ValueError(
            f"target has {count} voxels; this bounded bridge permits at most "
            f"{request.max_target_count}. Define a smaller explicit target ROI in Slicer "
            "or use a separately reviewed batch workflow. Do not crop the exported grid "
            "or silently subsample the target."
        )
    target = target_from_mask(target_mask, request.affine, stride=1)
    shape = target_mask.shape
    structures = []

    def geometry(path: Path) -> VoxelGeometry:
        return VoxelGeometry(
            uri=str(path), affine=request.affine, coordinate_frame="RAS",
            foreground_values=[1], refine_near_boundary=False,
        )

    bone_mask = None
    if request.bone is not None:
        path = _mask_path(request.bone, base)
        bone_mask = _load_binary(path, "bone", shape)
        structures.append(ProtectedStructure(
            name=BONE_NAME, geometry=geometry(path), tissue_type="bone",
            allow_virtual_removal=True,
        ))
    for item in request.protected:
        path = _mask_path(item.path, base)
        mask = _load_binary(path, f"protected.{item.name}", shape)
        if not np.any(mask):
            raise ValueError(f"protected.{item.name}: empty masks cannot declare protected anatomy")
        structures.append(ProtectedStructure(name=item.name, geometry=geometry(path)))
    if not request.completeness_declared:
        structures.append(ProtectedStructure(
            name=UNKNOWN_NAME, status=KnowledgeStatus.UNKNOWN, tissue_type="critical",
        ))

    removals = {}
    for name, value in request.removals.items():
        path = _mask_path(value, base)
        mask = _load_binary(path, f"removals.{name}", shape)
        if np.any((mask != 0) & (bone_mask == 0)):
            raise ValueError(f"removals.{name}: removal must be a subset of the bone mask")
        removals[name] = VirtualBoneRemoval(
            structure_name=BONE_NAME, geometry=geometry(path), reviewed=True,
        )
    approaches = []
    for name in ("EEA", "CTM"):
        entry = request.entries.get(name)
        if not entry:
            continue
        direction = np.asarray(request.target_point) - entry
        direction /= np.linalg.norm(direction)
        approaches.append(ApproachConfig(
            name=name, kind=ApproachKind.EEA if name == "EEA" else ApproachKind.TRANSMAXILLARY,
            portal=PortalDisk(
                center_mm=tuple(entry), normal=tuple(direction),
                radius_mm=request.portal_diameter_mm / 2,
            ),
            nominal_direction=tuple(direction),
            instrument=RigidInstrument(
                radius_mm=request.shaft_diameter_mm / 2,
                length_mm=request.instrument_length_mm,
            ),
            sampling=request.sampling,
            target_tolerance_mm=0.0,
            allow_unknown_anatomy=False,
            virtual_bone_removals=[removals[name]] if name in removals else [],
        ))
    return CorridorCase(
        case_id=request.case_id, coordinate_frame="RAS", target=target,
        protected_structures=structures, approaches=approaches,
    )


def _fingerprints(request: SlicerBridgeRequest, base: Path) -> list[dict]:
    references = [("target", request.target)]
    if request.bone:
        references.append(("bone", request.bone))
    references.extend((f"protected.{item.name}", item.path) for item in request.protected)
    references.extend((f"removals.{name}", path) for name, path in request.removals.items())
    return [
        {"role": role, "sha256": file_sha256(_mask_path(path, base))}
        for role, path in references
    ]


def run_request(request_path: str | Path, output_dir: str | Path) -> dict:
    """Run a JSON request and write analysis, coverage, case and bridge result files.

    Returns the same JSON-compatible envelope written to ``result.json``.
    An abstention is a valid analysis result, not a process failure. Malformed
    inputs raise ValueError/OSError; the CLI reports these on stderr and exits 2.
    """
    request_path = Path(request_path).resolve()
    request = _request(json.loads(request_path.read_text(encoding="utf-8")))
    base = request_path.parent
    before = _fingerprints(request, base)
    case = build_case(request, base_directory=base)
    result = analyze_case(case, base_directory=base)
    exact_paths = evaluate_exact_paths(
        case, request.exact_candidates, base_directory=base
    )
    if before != _fingerprints(request, base):
        raise ValueError("Exported masks changed during analysis; re-export and rerun")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    declaration = {
        "anatomy_complete_requested": request.anatomy_complete,
        "completeness_declared": request.completeness_declared,
        "reviewer": request.reviewer,
        "removals_reviewed": request.removals_reviewed,
        "automated_anatomy_approval": False,
        "note": (
            "Anatomy completeness and removal review are user declarations only, "
            "not automated approval, atlas validation, or evidence of clinical safety. "
            "Unknown critical anatomy forces abstention when the completeness "
            "declaration, reviewer, or protected masks are missing."
        ),
    }
    export_result(
        output / "analysis.json", case, result, base_directory=base,
        analysis_options={"slicer_bridge_schema_version": BRIDGE_SCHEMA_VERSION,
                          "review_declaration": declaration},
    )
    coverage = build_coverage_comparison(case, result)
    export_comparison(output, case, result)
    write_case(output / "case.json", case)
    envelope = {
        "bridge_schema_version": BRIDGE_SCHEMA_VERSION,
        "case_id": case.case_id,
        "coordinate_frame": "RAS",
        "units": "mm",
        "array_order": "xyz",
        "grid_shape": list(case.target.source_shape),
        "affine": [list(row) for row in request.affine],
        "input_files": before,
        "review_declaration": declaration,
        "sampling_caveat": SAMPLING_CAVEAT,
        "result": result.model_dump(mode="json"),
        "exact_paths": [path.model_dump(mode="json") for path in exact_paths],
        "coverage": coverage.to_dict(),
        "files": {
            "case": "case.json", "analysis": "analysis.json",
            "coverage_json": "comparison.json", "coverage_csv": "comparison_points.csv",
            "result": "result.json",
        },
    }
    atomic_json_write(output / "result.json", envelope)
    return envelope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path, help="JSON request path")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        envelope = run_request(args.request, args.output_dir)
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps({
        "result_path": str(args.output_dir.resolve() / "result.json"),
        "statuses": {item["name"]: item["status"] for item in envelope["result"]["approaches"]},
    }, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
