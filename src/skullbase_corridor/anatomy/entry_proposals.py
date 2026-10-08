"""Deterministic, review-required entry proposals from labelled anatomy masks.

This module proposes geometric landmarks.  It does not estimate safety,
operability, or the clinical appropriateness of an approach.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Mapping, TypeAlias

import numpy as np
from numpy.typing import NDArray
from pydantic import Field
from scipy import ndimage

from skullbase_corridor.domain.models import StrictModel, Vec3, validate_physical_affine


MaskArray: TypeAlias = NDArray[np.bool_]
VoxelIndex: TypeAlias = tuple[int, int, int]


@dataclass(frozen=True)
class AnatomyMask:
    """A mask on the common proposal grid, with its review provenance."""

    data: NDArray[np.generic]
    status: Literal["reviewed", "predicted"] = "reviewed"


class ProposalStatus(StrEnum):
    COMPLETE = "complete"
    CONDITIONAL = "conditional"
    ABSTAINED = "abstained"


class TargetLaterality(StrEnum):
    LEFT = "left"
    MIDLINE = "midline"
    RIGHT = "right"


class MidSagittalEstimate(StrictModel):
    point_ras_mm: Vec3
    left_to_right_normal: Vec3
    source_pairs: list[str] = Field(min_length=1)


class EntryCandidate(StrictModel):
    candidate_id: str
    approach: Literal["eea", "ctm"]
    side: Literal["left", "right"]
    entry_voxel: VoxelIndex
    entry_ras_mm: Vec3
    target_ras_mm: Vec3
    direction: Vec3
    entry_surface: Literal[
        "anterior_nasal_boundary",
        "anterior_maxillary_boundary",
    ]
    internal_medial_wall_voxel: VoxelIndex | None = None
    internal_medial_wall_ras_mm: Vec3 | None = None
    conditional_constraints: list[str] = Field(default_factory=list)
    source_statuses: dict[str, Literal["reviewed", "predicted"]] = Field(default_factory=dict)
    removal_evidence_id: str | None = None
    protected_intersections: list[str] = Field(default_factory=list)


class RemovalVoxelEvidence(StrictModel):
    """Unreviewed bone voxels implicated by a proposal, never an authorization."""

    evidence_id: str
    candidate_id: str
    structure: Literal["bone"] = "bone"
    voxels: list[VoxelIndex]
    reviewed: bool = False
    interpretation: Literal["proposed_removal_evidence"] = "proposed_removal_evidence"


class EntryProposalResult(StrictModel):
    status: ProposalStatus
    target_ras_mm: Vec3
    target_laterality: TargetLaterality | None
    contralateral_sides: list[Literal["left", "right"]]
    midsagittal: MidSagittalEstimate | None
    candidates: list[EntryCandidate] = Field(default_factory=list)
    removal_evidence: list[RemovalVoxelEvidence] = Field(default_factory=list)
    missing_constraints: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


_ALIASES = {
    "left_nasal": "left_nasal_cavity",
    "right_nasal": "right_nasal_cavity",
    "left_maxillary": "left_maxillary_sinus",
    "right_maxillary": "right_maxillary_sinus",
    "ica": "protected",
}
_BILATERAL_PAIRS = (
    ("nasal_cavity", "left_nasal_cavity", "right_nasal_cavity"),
    ("maxillary_sinus", "left_maxillary_sinus", "right_maxillary_sinus"),
    ("orbit", "left_orbit", "right_orbit"),
)


def _tuple3(value: NDArray[np.floating]) -> Vec3:
    return tuple(float(item) for item in value)  # type: ignore[return-value]


def _unit_direction(start: NDArray[np.float64], end: NDArray[np.float64]) -> Vec3 | None:
    vector = end - start
    length = float(np.linalg.norm(vector))
    if length <= 1e-12:
        return None
    return _tuple3(vector / length)


def _voxel_tuple(value: NDArray[np.integer]) -> VoxelIndex:
    return tuple(int(item) for item in value)  # type: ignore[return-value]


def _normalise_masks(
    masks: Mapping[str, NDArray[np.generic] | AnatomyMask],
) -> tuple[dict[str, MaskArray], dict[str, Literal["reviewed", "predicted"]], tuple[int, ...]]:
    arrays: dict[str, MaskArray] = {}
    statuses: dict[str, Literal["reviewed", "predicted"]] = {}
    shape: tuple[int, ...] | None = None
    for supplied_name, supplied in masks.items():
        name = _ALIASES.get(supplied_name, supplied_name)
        item = supplied if isinstance(supplied, AnatomyMask) else AnatomyMask(supplied)
        array = np.asarray(item.data)
        if array.ndim != 3:
            raise ValueError(f"{supplied_name} mask must be three-dimensional")
        if not (
            np.issubdtype(array.dtype, np.bool_)
            or np.issubdtype(array.dtype, np.integer)
            or np.issubdtype(array.dtype, np.floating)
        ):
            raise ValueError(f"{supplied_name} mask must be numeric or boolean")
        if np.issubdtype(array.dtype, np.floating) and not np.isfinite(array).all():
            raise ValueError(f"{supplied_name} mask must be finite")
        if shape is None:
            shape = array.shape
        elif array.shape != shape:
            raise ValueError("all anatomy masks must share one common grid")
        arrays[name] = np.asarray(array != 0, dtype=bool)
        statuses[name] = item.status
    if shape is None:
        raise ValueError("at least one anatomy mask is required")
    return arrays, statuses, shape


def _world(indices: NDArray[np.integer] | NDArray[np.floating], affine: NDArray[np.float64]):
    values = np.asarray(indices, dtype=float)
    return values @ affine[:3, :3].T + affine[:3, 3]


def _centroid(mask: MaskArray, affine: NDArray[np.float64]) -> NDArray[np.float64]:
    return np.asarray(_world(np.argwhere(mask).mean(axis=0), affine), dtype=float)


def _estimate_midline(
    masks: Mapping[str, MaskArray], affine: NDArray[np.float64]
) -> MidSagittalEstimate | None:
    midpoints: list[NDArray[np.float64]] = []
    directions: list[NDArray[np.float64]] = []
    sources: list[str] = []
    for label, left_name, right_name in _BILATERAL_PAIRS:
        left = masks.get(left_name)
        right = masks.get(right_name)
        if left is None or right is None or not left.any() or not right.any():
            continue
        left_center, right_center = _centroid(left, affine), _centroid(right, affine)
        difference = right_center - left_center
        length = float(np.linalg.norm(difference))
        if length <= 1e-6:
            continue
        midpoints.append((left_center + right_center) / 2.0)
        directions.append(difference / length)
        sources.append(label)
    if not sources:
        return None
    reference = directions[0]
    aligned = [direction if np.dot(direction, reference) >= 0 else -direction
               for direction in directions]
    normal = np.sum(aligned, axis=0)
    normal /= np.linalg.norm(normal)
    return MidSagittalEstimate(
        point_ras_mm=_tuple3(np.mean(midpoints, axis=0)),
        left_to_right_normal=_tuple3(normal),
        source_pairs=sources,
    )


def _directional_boundary(
    mask: MaskArray,
    affine: NDArray[np.float64],
    world_direction: NDArray[np.float64],
) -> MaskArray:
    """Return the exposed voxel face most aligned with a world-space direction."""

    columns = affine[:3, :3]
    unit_columns = columns / np.linalg.norm(columns, axis=0)
    projections = unit_columns.T @ world_direction
    axis = int(np.argmax(np.abs(projections)))
    step = 1 if projections[axis] >= 0 else -1
    neighbour = np.zeros_like(mask)
    source = [slice(None)] * 3
    destination = [slice(None)] * 3
    if step > 0:
        source[axis] = slice(1, None)
        destination[axis] = slice(None, -1)
    else:
        source[axis] = slice(None, -1)
        destination[axis] = slice(1, None)
    neighbour[tuple(destination)] = mask[tuple(source)]
    return mask & ~neighbour


def _external_bone_surface(
    cavity: MaskArray,
    bone: MaskArray,
    affine: NDArray[np.float64],
    world_direction: NDArray[np.float64],
) -> MaskArray:
    """Return bone voxels immediately outside a directional cavity boundary."""

    columns = affine[:3, :3]
    unit_columns = columns / np.linalg.norm(columns, axis=0)
    projections = unit_columns.T @ world_direction
    axis = int(np.argmax(np.abs(projections)))
    step = 1 if projections[axis] >= 0 else -1
    cavity_surface = _directional_boundary(cavity, affine, world_direction)
    outside = np.zeros_like(cavity)
    source = [slice(None)] * 3
    destination = [slice(None)] * 3
    if step > 0:
        source[axis] = slice(None, -1)
        destination[axis] = slice(1, None)
    else:
        source[axis] = slice(1, None)
        destination[axis] = slice(None, -1)
    outside[tuple(destination)] = cavity_surface[tuple(source)]
    return outside & bone


def _select_distinct(
    surface: MaskArray,
    affine: NDArray[np.float64],
    target: NDArray[np.float64],
    count: int,
    minimum_separation_mm: float,
) -> list[tuple[VoxelIndex, NDArray[np.float64]]]:
    indices = np.argwhere(surface)
    if len(indices) == 0:
        return []
    points = np.asarray(_world(indices, affine))
    distances = np.linalg.norm(points - target, axis=1)
    order = np.lexsort((indices[:, 2], indices[:, 1], indices[:, 0], distances))
    selected: list[int] = []
    for index in order:
        if all(np.linalg.norm(points[index] - points[prior]) >= minimum_separation_mm
               for prior in selected):
            selected.append(int(index))
            if len(selected) == count:
                break
    return [(_voxel_tuple(indices[index]), points[index]) for index in selected]


def _nearest_medial_wall(
    mask: MaskArray,
    affine: NDArray[np.float64],
    side: str,
    normal: NDArray[np.float64],
    entry: NDArray[np.float64],
    target: NDArray[np.float64],
) -> tuple[VoxelIndex, NDArray[np.float64]] | None:
    toward_midline = normal if side == "left" else -normal
    surface = _directional_boundary(mask, affine, toward_midline)
    indices = np.argwhere(surface)
    if len(indices) == 0:
        return None
    points = np.asarray(_world(indices, affine))
    distinct = np.linalg.norm(points - entry, axis=1) > 1e-6
    indices, points = indices[distinct], points[distinct]
    if len(indices) == 0:
        return None
    segment = target - entry
    denominator = float(np.dot(segment, segment))
    if denominator <= 1e-12:
        distances = np.linalg.norm(points - target, axis=1)
    else:
        factors = np.clip(((points - entry) @ segment) / denominator, 0.0, 1.0)
        distances = np.linalg.norm(points - (entry + factors[:, None] * segment), axis=1)
    order = np.lexsort((indices[:, 2], indices[:, 1], indices[:, 0], distances))
    chosen = int(order[0])
    return _voxel_tuple(indices[chosen]), points[chosen]


def _line_voxels(
    start: NDArray[np.float64],
    end: NDArray[np.float64],
    mask: MaskArray,
    affine: NDArray[np.float64],
) -> list[VoxelIndex]:
    inverse = np.linalg.inv(affine)
    spacing = np.linalg.norm(affine[:3, :3], axis=0)
    steps = max(2, int(np.ceil(np.linalg.norm(end - start) / (0.5 * min(spacing)))) + 1)
    points = start + np.linspace(0.0, 1.0, steps)[:, None] * (end - start)
    homogeneous = np.column_stack((points, np.ones(len(points))))
    voxels = np.rint((homogeneous @ inverse.T)[:, :3]).astype(int)
    valid = np.all((voxels >= 0) & (voxels < np.asarray(mask.shape)), axis=1)
    voxels = voxels[valid]
    selected = {_voxel_tuple(voxel) for voxel in voxels if mask[tuple(voxel)]}
    return sorted(selected)


def _protected_intersections(
    start: NDArray[np.float64],
    target: NDArray[np.float64],
    masks: Mapping[str, MaskArray],
    affine: NDArray[np.float64],
) -> list[str]:
    names = ["protected", "left_ica", "right_ica", "ica"]
    return sorted(
        name for name in names
        if name in masks and masks[name].any() and _line_voxels(start, target, masks[name], affine)
    )


def propose_entry_candidates(
    target_ras_mm: Vec3,
    masks: Mapping[str, NDArray[np.generic] | AnatomyMask],
    affine: NDArray[np.floating],
    *,
    candidates_per_surface: int = 3,
    minimum_separation_mm: float = 3.0,
    midline_tolerance_mm: float | None = None,
) -> EntryProposalResult:
    """Propose target-driven EEA and contralateral transmaxillary landmarks.

    ``masks`` accepts the canonical keys ``left_nasal_cavity``,
    ``right_nasal_cavity``, ``left_maxillary_sinus``,
    ``right_maxillary_sinus``, ``bone``, optional ``hard_palate``,
    ``upper_teeth``, ``left_orbit``, ``right_orbit``, ``left_ica``,
    ``right_ica``, and/or ``protected``. Arrays and :class:`AnatomyMask`
    values may be mixed.
    """

    matrix = np.asarray(affine, dtype=float)
    validate_physical_affine(matrix)
    target = np.asarray(target_ras_mm, dtype=float)
    if target.shape != (3,) or not np.isfinite(target).all():
        raise ValueError("target_ras_mm must be a finite RAS point")
    if candidates_per_surface < 1:
        raise ValueError("candidates_per_surface must be positive")
    if not np.isfinite(minimum_separation_mm) or minimum_separation_mm < 0:
        raise ValueError("minimum_separation_mm must be finite and nonnegative")
    arrays, statuses, shape = _normalise_masks(masks)
    midline = _estimate_midline(arrays, matrix)
    if midline is None:
        return EntryProposalResult(
            status=ProposalStatus.ABSTAINED,
            target_ras_mm=_tuple3(target),
            target_laterality=None,
            contralateral_sides=[],
            midsagittal=None,
            missing_constraints=[
                "midsagittal plane unavailable: provide a nonempty left/right nasal, "
                "maxillary, or orbit pair"
            ],
            notes=["No entry is proposed without a bilateral-anatomy midsagittal estimate."],
        )

    normal = np.asarray(midline.left_to_right_normal)
    plane_point = np.asarray(midline.point_ras_mm)
    signed_target_distance = float(np.dot(target - plane_point, normal))
    if midline_tolerance_mm is None:
        midline_tolerance_mm = max(1.0, float(np.min(np.linalg.norm(matrix[:3, :3], axis=0))))
    if not np.isfinite(midline_tolerance_mm) or midline_tolerance_mm < 0:
        raise ValueError("midline_tolerance_mm must be finite and nonnegative")
    if abs(signed_target_distance) <= midline_tolerance_mm:
        laterality = TargetLaterality.MIDLINE
        contralateral_sides: list[Literal["left", "right"]] = ["left", "right"]
    elif signed_target_distance > 0:
        laterality = TargetLaterality.RIGHT
        contralateral_sides = ["left"]
    else:
        laterality = TargetLaterality.LEFT
        contralateral_sides = ["right"]

    candidates: list[EntryCandidate] = []
    removals: list[RemovalVoxelEvidence] = []
    missing: list[str] = []
    anterior = np.asarray([0.0, 1.0, 0.0])

    protected_present = any(
        name in arrays and arrays[name].any()
        for name in ("protected", "left_ica", "right_ica")
    )
    global_conditions = [] if protected_present else [
        "ICA/protected mask missing; protected-structure clearance is unknown"
    ]
    optional_missing = [
        name for name in ("hard_palate", "upper_teeth")
        if name not in arrays or not arrays[name].any()
    ]
    if not any(name in arrays and arrays[name].any()
               for name in ("left_orbit", "right_orbit", "orbit")):
        optional_missing.append("orbit")
    missing.extend(
        f"{name} missing or empty; related anatomical constraint is unknown"
        for name in optional_missing
    )

    for side in ("left", "right"):
        nasal_name = f"{side}_nasal_cavity"
        nasal = arrays.get(nasal_name)
        if nasal is None or not nasal.any():
            missing.append(f"{nasal_name} missing or empty; {side} EEA not proposed")
            continue
        surface = _directional_boundary(nasal, matrix, anterior)
        selected = _select_distinct(
            surface, matrix, target, candidates_per_surface, minimum_separation_mm
        )
        for sequence, (voxel, point) in enumerate(selected, start=1):
            candidate_id = f"eea-{side}-{sequence}"
            direction = _unit_direction(point, target)
            if direction is None:
                missing.append(f"{candidate_id} coincides with target and was omitted")
                continue
            intersections = _protected_intersections(point, target, arrays, matrix)
            conditions = list(global_conditions)
            if statuses[nasal_name] == "predicted":
                conditions.append(f"{nasal_name} is predicted and requires review")
            conditions.extend(
                f"{name} mask missing; related constraint is unknown"
                for name in optional_missing if name in ("hard_palate", "upper_teeth")
            )
            conditions.extend(
                f"centerline intersects {name}; proposal requires review or rejection"
                for name in intersections
            )
            candidates.append(EntryCandidate(
                candidate_id=candidate_id,
                approach="eea",
                side=side,
                entry_voxel=voxel,
                entry_ras_mm=_tuple3(point),
                target_ras_mm=_tuple3(target),
                direction=direction,
                entry_surface="anterior_nasal_boundary",
                conditional_constraints=conditions,
                source_statuses={nasal_name: statuses[nasal_name]},
                protected_intersections=intersections,
            ))

    bone = arrays.get("bone")
    if bone is None or not bone.any():
        missing.append("bone missing or empty; CTM not proposed")
    else:
        for side in contralateral_sides:
            sinus_name = f"{side}_maxillary_sinus"
            sinus = arrays.get(sinus_name)
            if sinus is None or not sinus.any():
                missing.append(f"{sinus_name} missing or empty; {side} CTM not proposed")
                continue
            external_surface = _external_bone_surface(sinus, bone, matrix, anterior)
            if not external_surface.any():
                missing.append(
                    f"{sinus_name} has no adjacent anterior bone; {side} CTM not proposed"
                )
                continue
            selected = _select_distinct(
                external_surface, matrix, target, candidates_per_surface,
                minimum_separation_mm,
            )
            for sequence, (entry_voxel, entry_point) in enumerate(selected, start=1):
                medial = _nearest_medial_wall(
                    sinus, matrix, side, normal, entry_point, target
                )
                if medial is None:
                    continue
                medial_voxel, medial_point = medial
                candidate_id = f"ctm-{side}-{sequence}"
                evidence_id = f"removal-{candidate_id}"
                removal_voxels = _line_voxels(entry_point, medial_point, bone, matrix)
                direction = _unit_direction(entry_point, target)
                if direction is None:
                    missing.append(f"{candidate_id} coincides with target and was omitted")
                    continue
                intersections = _protected_intersections(entry_point, target, arrays, matrix)
                conditions = list(global_conditions)
                conditions.extend(
                    f"{name} mask missing; related constraint is unknown"
                    for name in optional_missing
                )
                if statuses[sinus_name] == "predicted":
                    conditions.append(f"{sinus_name} is predicted and requires review")
                if statuses["bone"] == "predicted":
                    conditions.append("bone is predicted and requires review")
                if not removal_voxels:
                    conditions.append(
                        "no bone voxels found between external entry and medial-wall crossing"
                    )
                conditions.extend(
                    f"centerline intersects {name}; proposal requires review or rejection"
                    for name in intersections
                )
                candidates.append(EntryCandidate(
                    candidate_id=candidate_id,
                    approach="ctm",
                    side=side,
                    entry_voxel=entry_voxel,
                    entry_ras_mm=_tuple3(entry_point),
                    target_ras_mm=_tuple3(target),
                    direction=direction,
                    entry_surface="anterior_maxillary_boundary",
                    internal_medial_wall_voxel=medial_voxel,
                    internal_medial_wall_ras_mm=_tuple3(medial_point),
                    conditional_constraints=conditions,
                    source_statuses={
                        sinus_name: statuses[sinus_name],
                        "bone": statuses["bone"],
                    },
                    removal_evidence_id=evidence_id,
                    protected_intersections=intersections,
                ))
                removals.append(RemovalVoxelEvidence(
                    evidence_id=evidence_id,
                    candidate_id=candidate_id,
                    voxels=removal_voxels,
                ))

    if not candidates:
        status = ProposalStatus.ABSTAINED
    elif missing or any(candidate.conditional_constraints for candidate in candidates):
        status = ProposalStatus.CONDITIONAL
    else:
        status = ProposalStatus.COMPLETE
    return EntryProposalResult(
        status=status,
        target_ras_mm=_tuple3(target),
        target_laterality=laterality,
        contralateral_sides=contralateral_sides,
        midsagittal=midline,
        candidates=candidates,
        removal_evidence=removals,
        missing_constraints=missing,
        notes=[
            "Candidates are deterministic geometric proposals, not safety probabilities.",
            "Removal voxels are separate unreviewed evidence and do not authorize removal.",
            f"Common mask grid shape: {shape}.",
        ],
    )


def propose_entries(*args, **kwargs) -> EntryProposalResult:
    """Backward-friendly concise alias for :func:`propose_entry_candidates`."""

    return propose_entry_candidates(*args, **kwargs)
