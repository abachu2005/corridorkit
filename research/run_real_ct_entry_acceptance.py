#!/usr/bin/env python3
"""Run one public individual-CT entry acceptance replay.

This is a geometry/software acceptance runner, not a safety analysis.  NasalSeg
provides air-space labels, TotalSegmentator outputs are predictions, and the
declared target is a deterministic nasopharynx landmark (not a tumor).
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
from pathlib import Path
import platform
import sys
import time
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import nibabel as nib
import numpy as np
from scipy import ndimage

from skullbase_corridor.anatomy.entry_proposals import AnatomyMask, propose_entry_candidates
from skullbase_corridor.domain.models import (
    ApproachConfig,
    ApproachKind,
    CorridorCase,
    ExactPathCandidate,
    ImageReference,
    KnowledgeStatus,
    PortalDisk,
    ProtectedStructure,
    RigidInstrument,
    SamplingConfig,
    TargetPointCloud,
    VoxelGeometry,
)
from skullbase_corridor.export.json import (
    atomic_json_write,
    canonical_json,
    file_sha256,
    software_version,
)
from skullbase_corridor.geometry.engine import evaluate_exact_paths
from skullbase_corridor.io.volumes import (
    Volume,
    load_volume,
    resample_to_grid,
    validate_alignment,
    validate_labels,
)

LABEL_MAPPING = {
    1: "right_maxillary_sinus",
    2: "left_maxillary_sinus",
    3: "right_nasal_cavity",
    4: "left_nasal_cavity",
    5: "nasopharynx",
}
WARNING = (
    "PUBLIC INDIVIDUAL CT SOFTWARE ACCEPTANCE ONLY. Predicted anatomy and an "
    "air-space computational target do not establish a safe or clinically usable path."
)
ICA_ALIASES = {
    "left": (
        "internal_carotid_artery_left", "carotid_artery_internal_left",
        "internal_carotid_left", "ica_left", "left_internal_carotid_artery",
    ),
    "right": (
        "internal_carotid_artery_right", "carotid_artery_internal_right",
        "internal_carotid_right", "ica_right", "right_internal_carotid_artery",
    ),
}
BONE_NAMES = (
    "skull", "maxilla", "maxilla_left", "maxilla_right", "mandible",
    "zygomatic_arch_left", "zygomatic_arch_right", "pterygoid_process_left",
    "pterygoid_process_right", "teeth_upper", "upper_teeth",
)
OPTIONAL_PROPOSAL_NAMES = {
    "hard_palate": ("hard_palate",),
    "upper_teeth": ("teeth_upper", "upper_teeth"),
    "left_orbit": ("eye_left", "orbit_left"),
    "right_orbit": ("eye_right", "orbit_right"),
}
UNAVAILABLE_CRITICAL = (
    "optic nerves", "cavernous sinus contents", "cranial nerves",
    "ophthalmic arteries", "unsegmented critical anatomy",
)


@dataclass(frozen=True)
class RunnerConfig:
    instrument_length_mm: float = 200.0
    shaft_radius_mm: float = 1.0
    portal_radius_mm: float = 4.0
    max_angle_deg: float = 1.0
    candidates_per_surface: int = 2
    minimum_separation_mm: float = 3.0


def _normal_name(path: Path) -> str:
    name = path.name.lower()
    for suffix in (".nii.gz", ".nii"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.replace("-", "_").replace(" ", "_")


def discover_totalsegmentator_masks(directory: Path) -> dict[str, Path]:
    """Index NIfTI masks recursively and reject ambiguous normalized names."""
    if not directory.is_dir():
        raise FileNotFoundError(f"TotalSegmentator mask directory unavailable: {directory}")
    result: dict[str, Path] = {}
    for path in sorted(directory.rglob("*.nii")) + sorted(directory.rglob("*.nii.gz")):
        name = _normal_name(path)
        if name in result and result[name] != path:
            raise ValueError(f"Ambiguous TotalSegmentator mask name {name!r}")
        result[name] = path
    if not result:
        raise ValueError("TotalSegmentator directory contains no NIfTI masks")
    return result


def _first(index: dict[str, Path], names: tuple[str, ...]) -> Path | None:
    return next((index[name] for name in names if name in index), None)


def _binary_on_reference(path: Path, reference: Volume) -> np.ndarray:
    volume = load_volume(path, assume_spatial_unit="mm")
    validate_labels(volume)
    if volume.data.shape != reference.data.shape or not np.allclose(
        volume.affine, reference.affine, atol=1e-4, rtol=0
    ):
        volume = resample_to_grid(volume, reference, labels=True)
    return np.asarray(volume.data != 0, dtype=bool)


def select_declared_target(mask: np.ndarray, affine: np.ndarray) -> tuple[float, float, float]:
    """Select an interior voxel near posterior/superior nasopharynx in RAS.

    Candidates are the posterior 35% (low RAS Y) and superior 35% (high RAS Z)
    of label 5.  Maximum interior Euclidean distance wins, then posterior,
    superior, and xyz voxel index deterministically break ties.
    """
    indices = np.argwhere(mask)
    if not len(indices):
        raise ValueError("NasalSeg label 5 (nasopharynx) is empty")
    world = indices @ affine[:3, :3].T + affine[:3, 3]
    posterior_cut = np.quantile(world[:, 1], 0.35)
    superior_cut = np.quantile(world[:, 2], 0.65)
    eligible = (world[:, 1] <= posterior_cut) & (world[:, 2] >= superior_cut)
    if not eligible.any():
        eligible = np.ones(len(indices), dtype=bool)
    distance = ndimage.distance_transform_edt(
        mask, sampling=np.linalg.norm(affine[:3, :3], axis=0)
    )
    chosen_indices = indices[eligible]
    chosen_world = world[eligible]
    depths = distance[tuple(chosen_indices.T)]
    order = np.lexsort((
        chosen_indices[:, 2], chosen_indices[:, 1], chosen_indices[:, 0],
        -chosen_world[:, 2], chosen_world[:, 1], -depths,
    ))
    return tuple(float(value) for value in chosen_world[order[0]])  # type: ignore[return-value]


def _save_mask(path: Path, data: np.ndarray, affine: np.ndarray, description: str) -> None:
    image = nib.Nifti1Image(np.asarray(data, dtype=np.uint8), affine)
    image.set_qform(affine, code=1)
    image.set_sform(affine, code=1)
    image.header.set_xyzt_units("mm")
    image.header["descrip"] = description.encode("ascii", errors="replace")[:79]
    nib.save(image, path)


def _geometry(path: Path, affine: np.ndarray) -> VoxelGeometry:
    return VoxelGeometry(
        uri=path.name,
        affine=tuple(tuple(float(x) for x in row) for row in affine),
        foreground_values=[1],
        coordinate_frame="RAS",
    )


def _artifact(path: Path, root: Path) -> dict:
    return {
        "path": str(path.relative_to(root)),
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def _git_revision() -> str | None:
    import subprocess

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def run_acceptance(
    ct_path: str | Path,
    label_path: str | Path,
    totalsegmentator_directory: str | Path,
    output_directory: str | Path,
    *,
    case_id: str,
    config: RunnerConfig = RunnerConfig(),
) -> Path:
    """Execute the replay and return ``report.json``."""
    ct_path = Path(ct_path).resolve()
    label_path = Path(label_path).resolve()
    ts_directory = Path(totalsegmentator_directory).resolve()
    output = Path(output_directory).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Refusing nonempty output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    @contextmanager
    def stage(name: str) -> Iterator[None]:
        started = time.perf_counter()
        yield
        timings[name] = round(time.perf_counter() - started, 6)

    with stage("load_and_validate_inputs"):
        ct = load_volume(ct_path, assume_spatial_unit="mm")
        labels = load_volume(label_path, assume_spatial_unit="mm")
        validate_alignment(ct, labels)
        validate_labels(labels)
        observed = set(int(value) for value in np.unique(labels.data))
        if not set(LABEL_MAPPING).issubset(observed):
            raise ValueError(
                f"NasalSeg manual label must contain labels 1..5; observed {sorted(observed)}"
            )
        index = discover_totalsegmentator_masks(ts_directory)

    with stage("build_common_ras_masks"):
        masks: dict[str, np.ndarray] = {
            name: np.asarray(labels.data == value)
            for value, name in LABEL_MAPPING.items()
        }
        loaded_sources: dict[str, list[str]] = {}
        bone_parts = []
        for name in BONE_NAMES:
            if name in index:
                bone_parts.append(_binary_on_reference(index[name], ct))
                loaded_sources.setdefault("bone", []).append(str(index[name]))
        if bone_parts:
            masks["bone"] = np.logical_or.reduce(bone_parts)
        for side, aliases in ICA_ALIASES.items():
            path = _first(index, aliases)
            if path:
                key = f"{side}_ica"
                masks[key] = _binary_on_reference(path, ct)
                loaded_sources[key] = [str(path)]
        ica_parts = [masks[key] for key in ("left_ica", "right_ica") if key in masks]
        if ica_parts:
            masks["protected"] = np.logical_or.reduce(ica_parts)
        for key, aliases in OPTIONAL_PROPOSAL_NAMES.items():
            path = _first(index, aliases)
            if path:
                masks[key] = _binary_on_reference(path, ct)
                loaded_sources[key] = [str(path)]
        target = select_declared_target(masks["nasopharynx"], ct.affine)

    with stage("write_slicer_masks"):
        mask_paths: dict[str, Path] = {}
        for name, data in sorted(masks.items()):
            path = output / f"{name}.nii.gz"
            _save_mask(path, data, ct.affine, f"{name}; RAS mm; software acceptance")
            mask_paths[name] = path
        target_data = np.zeros(ct.data.shape, dtype=np.uint8)
        inverse = np.linalg.inv(ct.affine)
        target_voxel = np.rint((inverse @ np.array([*target, 1.0]))[:3]).astype(int)
        target_data[tuple(target_voxel)] = 1
        target_path = output / "target_landmark_NOT_TUMOR.nii.gz"
        _save_mask(target_path, target_data, ct.affine, "nasopharynx landmark; NOT TUMOR")

    with stage("propose_entries"):
        proposal_masks = {
            name: AnatomyMask(data, status=("reviewed" if name in LABEL_MAPPING.values()
                                            else "predicted"))
            for name, data in masks.items()
            if name != "nasopharynx"
        }
        proposal = propose_entry_candidates(
            target,
            proposal_masks,
            ct.affine,
            candidates_per_surface=config.candidates_per_surface,
            minimum_separation_mm=config.minimum_separation_mm,
        )

    source_image = ImageReference(uri=str(ct_path), modality="CT", sha256=file_sha256(ct_path))
    unavailable = [
        ProtectedStructure(name=name, status=KnowledgeStatus.UNKNOWN)
        for name in UNAVAILABLE_CRITICAL
    ]
    if "bone" not in masks:
        unavailable.append(ProtectedStructure(
            name="bone/maxilla constraint", status=KnowledgeStatus.UNKNOWN, tissue_type="bone"
        ))
    if "left_ica" not in masks:
        unavailable.append(ProtectedStructure(
            name="left internal carotid artery", status=KnowledgeStatus.UNKNOWN
        ))
    if "right_ica" not in masks:
        unavailable.append(ProtectedStructure(
            name="right internal carotid artery", status=KnowledgeStatus.UNKNOWN
        ))

    case_records = []
    exact_records = []
    cases_directory = output / "cases"
    cases_directory.mkdir()
    with stage("evaluate_exact_paths"):
        for candidate in proposal.candidates:
            direction = tuple(float(x) for x in candidate.direction)
            approach_name = candidate.candidate_id
            structures = list(unavailable)
            if "bone" in masks:
                structures.append(ProtectedStructure(
                    name="predicted conservative bone/maxilla union",
                    geometry=_geometry(mask_paths["bone"], ct.affine),
                    tissue_type="bone",
                ))
            if "protected" in masks:
                structures.append(ProtectedStructure(
                    name="predicted available ICA union",
                    geometry=_geometry(mask_paths["protected"], ct.affine),
                ))
            approach = ApproachConfig(
                name=approach_name,
                kind=(ApproachKind.EEA if candidate.approach == "eea"
                      else ApproachKind.TRANSMAXILLARY),
                portal=PortalDisk(
                    center_mm=candidate.entry_ras_mm,
                    normal=direction,
                    radius_mm=config.portal_radius_mm,
                ),
                nominal_direction=direction,
                instrument=RigidInstrument(
                    length_mm=config.instrument_length_mm,
                    radius_mm=config.shaft_radius_mm,
                ),
                sampling=SamplingConfig(
                    max_angle_deg=config.max_angle_deg,
                    polar_steps=1,
                    azimuth_steps=1,
                    target_directed=False,
                    adaptive_levels=0,
                ),
                target_tolerance_mm=0,
                allow_unknown_anatomy=False,
            )
            case = CorridorCase(
                case_id=f"{case_id}-{candidate.candidate_id}",
                source_image=source_image,
                target=TargetPointCloud(
                    points_mm=[target],
                    source="declared posterior/superior nasopharynx landmark; NOT TUMOR",
                ),
                protected_structures=structures,
                approaches=[approach],
            )
            exact = ExactPathCandidate(
                candidate_id=candidate.candidate_id,
                approach_name=approach_name,
                entry_point_mm=candidate.entry_ras_mm,
                target_point_mm=target,
            )
            result = evaluate_exact_paths(case, [exact], base_directory=output)[0]
            case_path = cases_directory / f"{candidate.candidate_id}.json"
            atomic_json_write(case_path, {
                "warning": WARNING,
                "case": case.model_dump(mode="json"),
                "exact_candidate": exact.model_dump(mode="json"),
                "proposal_internal_medial_wall_ras_mm": candidate.internal_medial_wall_ras_mm,
                "proposal_conditional_constraints": candidate.conditional_constraints,
            })
            case_records.append({
                "candidate_id": candidate.candidate_id,
                "case_artifact": str(case_path.relative_to(output)),
            })
            exact_records.append(result.model_dump(mode="json"))

    replay = {
        "case_id": case_id,
        "coordinate_frame": "RAS",
        "units": "mm",
        "nasalseg_label_mapping": LABEL_MAPPING,
        "target": {
            "ras_mm": target,
            "is_tumor": False,
            "source": "manual NasalSeg label 5",
            "algorithm": (
                "posterior 35% and superior 35% intersection; maximum physical "
                "interior distance; deterministic posterior/superior/xyz tie breaks"
            ),
        },
        "configuration": config.__dict__,
        "inputs": {
            "ct": {"path": str(ct_path), "sha256": file_sha256(ct_path)},
            "manual_label": {"path": str(label_path), "sha256": file_sha256(label_path)},
            "totalsegmentator_directory": str(ts_directory),
            "used_predicted_mask_sources": loaded_sources,
            "used_predicted_mask_hashes": {
                role: [{"path": item, "sha256": file_sha256(Path(item))}
                       for item in paths]
                for role, paths in loaded_sources.items()
            },
        },
    }
    request_path = output / "request.json"
    atomic_json_write(request_path, replay)

    artifacts = [
        request_path, target_path, *mask_paths.values(),
        *(output / item["case_artifact"] for item in case_records),
    ]
    report = {
        "schema": "real-ct-entry-acceptance-v1",
        "warning": WARNING,
        "safety_claim": False,
        "clinical_acceptance": False,
        "public_individual_ct": True,
        "synthetic": False,
        "averaged_atlas": False,
        "replay": replay,
        "replay_sha256": __import__("hashlib").sha256(canonical_json(replay)).hexdigest(),
        "proposal": proposal.model_dump(mode="json"),
        "exact_paths": exact_records,
        "cases": case_records,
        "knowledge": {
            "ica_union": "available_predicted" if "protected" in masks else "unavailable",
            "bone_maxilla_constraint": "available_predicted" if "bone" in masks else "unavailable",
            "unavailable_or_conditional": [item.name for item in unavailable],
            "interpretation": (
                "blocked means collision under declared masks; conditional/unavailable means "
                "knowledge is incomplete. No state is a safety finding."
            ),
        },
        "stage_timings_seconds_observational_not_replay_hash": timings,
        "provenance": {
            "software_version": software_version(),
            "git_revision": _git_revision(),
            "python": sys.version,
            "platform": platform.platform(),
            "runner": str(Path(__file__).resolve()),
            "runner_sha256": file_sha256(Path(__file__).resolve()),
            "total_segmentator_tasks_required": [
                "craniofacial_structures", "headneck_bones_vessels",
            ],
            "mask_semantics": (
                "NasalSeg labels are manual per supplied mapping; TotalSegmentator masks "
                "are predicted and are not promoted to reviewed anatomy."
            ),
        },
        "artifacts": [_artifact(path, output) for path in sorted(artifacts)],
    }
    report_path = output / "report.json"
    atomic_json_write(report_path, report)
    atomic_json_write(output / "sha256-manifest.json", {
        "files": [_artifact(path, output) for path in sorted([*artifacts, report_path])]
    })
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ct", required=True, type=Path, help="Individual NasalSeg image")
    parser.add_argument("--manual-label", required=True, type=Path, help="Paired manual label")
    parser.add_argument("--totalsegmentator-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--instrument-length-mm", type=float, default=200.0)
    parser.add_argument("--shaft-radius-mm", type=float, default=1.0)
    parser.add_argument("--portal-radius-mm", type=float, default=4.0)
    parser.add_argument("--candidates-per-surface", type=int, default=2)
    args = parser.parse_args()
    config = RunnerConfig(
        instrument_length_mm=args.instrument_length_mm,
        shaft_radius_mm=args.shaft_radius_mm,
        portal_radius_mm=args.portal_radius_mm,
        candidates_per_surface=args.candidates_per_surface,
    )
    print(run_acceptance(
        args.ct, args.manual_label, args.totalsegmentator_dir, args.output,
        case_id=args.case_id, config=config,
    ))


if __name__ == "__main__":
    main()
