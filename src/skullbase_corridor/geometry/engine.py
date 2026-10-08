"""Deterministic finite-instrument corridor analysis."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Callable

import numpy as np

from skullbase_corridor.domain.models import (
    AnalysisStatus,
    ApproachConfig,
    ApproachResult,
    CaseResult,
    CombinationResult,
    CorridorCase,
    ExactPathCandidate,
    ExactPathResult,
    ExactPathState,
    KnowledgeStatus,
    SimultaneousPairResult,
    SphereGeometry,
    TrajectoryResult,
    VoxelGeometry,
)
from skullbase_corridor.geometry.primitives import (
    angle_degrees,
    capsule_capsule_clearance,
    capsule_sphere_clearance,
    orthonormal_basis,
    portal_allows_direction,
    sample_spherical_cap,
    spherical_cap_sample_weights,
)
from skullbase_corridor.geometry.voxel import VoxelMaskBackend

TOL = 1e-9
CancelCheck = Callable[[], bool]


class AnalysisCancelled(RuntimeError):
    """Raised when the caller's cancellation callback returns true."""


@dataclass
class _PreparedGeometry:
    spheres: list[tuple[str, SphereGeometry]]
    voxel_backends: list[tuple[str, VoxelMaskBackend]]
    unavailable: list[str]
    unknown_anatomy: list[str]
    removal_assumptions: list[str]


def _check_cancel(cancel_check: CancelCheck | None) -> None:
    if cancel_check is not None and cancel_check():
        raise AnalysisCancelled("Corridor analysis cancelled")


def _prepare_geometry(
    case: CorridorCase,
    config: ApproachConfig,
    *,
    base_directory: Path | None,
    cancel_check: CancelCheck | None,
) -> _PreparedGeometry:
    unknown = [
        f"{structure.name}:{structure.status.value}"
        for structure in case.protected_structures
        if structure.status != KnowledgeStatus.KNOWN
    ]
    unavailable = [
        f"{structure.name}:unsupported-{structure.geometry.kind}"
        for structure in case.protected_structures
        if structure.status == KnowledgeStatus.KNOWN
        and not isinstance(structure.geometry, (SphereGeometry, VoxelGeometry))
        and structure.geometry is not None
    ]
    spheres = [
        (structure.name, structure.geometry)
        for structure in case.protected_structures
        if structure.status == KnowledgeStatus.KNOWN
        and isinstance(structure.geometry, SphereGeometry)
    ]
    voxel_backends: list[tuple[str, VoxelMaskBackend]] = []
    removals = {removal.structure_name: removal for removal in config.virtual_bone_removals}
    applied_removals: list[str] = []
    for structure in case.protected_structures:
        _check_cancel(cancel_check)
        if structure.status != KnowledgeStatus.KNOWN or not isinstance(
            structure.geometry, VoxelGeometry
        ):
            continue
        removal = removals.get(structure.name)
        try:
            voxel_backends.append((
                structure.name,
                VoxelMaskBackend(
                    structure.geometry,
                    base_directory=base_directory,
                    removal=removal.geometry if removal else None,
                    allow_bone_removal=bool(
                        removal
                        and removal.reviewed
                        and structure.allow_virtual_removal
                        and structure.tissue_type == "bone"
                    ),
                ),
            ))
            if removal is not None:
                applied_removals.append(structure.name)
        except (OSError, ValueError, ImportError) as exc:
            unavailable.append(
                f"{structure.name}: protected mask unavailable or invalid: {exc}"
            )
    return _PreparedGeometry(
        spheres=spheres,
        voxel_backends=voxel_backends,
        unavailable=unavailable,
        unknown_anatomy=unknown,
        removal_assumptions=applied_removals,
    )


def _capsule_clearance(
    prepared: _PreparedGeometry,
    entry: np.ndarray,
    tip: np.ndarray,
    radius_mm: float,
    *,
    cancel_check: CancelCheck | None,
) -> tuple[float, list[str]]:
    clearances = [
        capsule_sphere_clearance(
            entry, tip, radius_mm, np.asarray(sphere.center_mm), sphere.radius_mm
        )
        for _, sphere in prepared.spheres
    ]
    out_of_fov: list[str] = []
    for name, backend in prepared.voxel_backends:
        _check_cancel(cancel_check)
        query = backend.capsule_clearance(entry, tip, radius_mm)
        if query.out_of_fov or query.clearance_mm is None:
            out_of_fov.append(name)
        else:
            clearances.append(query.clearance_mm)
    return min(clearances, default=math.inf), out_of_fov


def _unavailable_result(case, config, notes, status=AnalysisStatus.ABSTAINED):
    return ApproachResult(
        name=config.name, kind=config.kind, status=status,
        target_count=len(case.target.points_mm), reached_point_indices=[],
        reached_measure_mm3=None, feasible_trajectory_count=0,
        feasible_solid_angle_sr=None, witness_direction=None,
        best_working_depth_mm=None, minimum_clearance_mm=None, trajectories=[],
        notes=notes,
    )


def _direction_components(direction: np.ndarray, nominal: np.ndarray) -> tuple[float, float]:
    polar = angle_degrees(direction, nominal)
    if polar <= TOL:
        return polar, 0.0
    first, second = orthonormal_basis(nominal)
    azimuth = math.degrees(math.atan2(float(direction @ second), float(direction @ first)))
    return polar, azimuth


def analyze_approach(
    case: CorridorCase,
    config: ApproachConfig,
    *,
    base_directory: Path | None = None,
    cancel_check: CancelCheck | None = None,
) -> ApproachResult:
    # Revalidate even model_copy(update=...) inputs (Pydantic does not validate copies).
    case = CorridorCase.model_validate(case.model_dump())
    config = ApproachConfig.model_validate(config.model_dump())
    if config.name not in {a.name for a in case.approaches}:
        raise ValueError("approach must belong to the case")
    checked_case = case.model_copy(update={"approaches": [config]})
    CorridorCase.model_validate(checked_case.model_dump())
    _check_cancel(cancel_check)
    target = case.target.array()
    prepared = _prepare_geometry(
        case, config, base_directory=base_directory, cancel_check=cancel_check
    )
    labels = prepared.unknown_anatomy + prepared.unavailable
    if labels and not config.allow_unknown_anatomy:
        return _unavailable_result(case, config, [
            "Incomplete protected-anatomy knowledge: " + ", ".join(labels)
        ])

    portal = np.asarray(config.portal.center_mm)
    instrument = config.instrument
    directions = sample_spherical_cap(
        np.asarray(config.nominal_direction),
        config.sampling.max_angle_deg,
        config.sampling.polar_steps,
        config.sampling.azimuth_steps,
    )
    trajectories: list[TrajectoryResult] = []
    reached_union: set[int] = set()
    best: TrajectoryResult | None = None
    has_out_of_fov = False
    seen: dict[tuple, TrajectoryResult] = {}
    nominal = np.asarray(config.nominal_direction)
    normal = np.asarray(config.portal.normal)
    # A deployed radial working tip is conservatively enclosed for the whole
    # insertion, not treated as collision-free reach around the shaft.
    collision_radius = max(instrument.radius_mm, instrument.tip_working_radius_mm)

    def evaluate(direction, entry, source):
        nonlocal has_out_of_fov
        _check_cancel(cancel_check)
        direction_tuple = tuple(float(x) for x in direction)
        entry_tuple = tuple(float(x) for x in entry)
        common = dict(direction=direction_tuple, entry_point_mm=entry_tuple,
                      sampling_source=source, conditional=bool(labels))
        offset = float(np.linalg.norm(entry - portal))
        if not portal_allows_direction(
            direction,
            normal,
            config.portal.radius_mm - offset,
            collision_radius,
        ):
            return TrajectoryResult(**common, feasible=False, reason="portal_aperture")
        axial = (target - entry) @ direction
        radial = np.linalg.norm((target - entry) - axial[:, None] * direction, axis=1)
        # Tip working radius is a reach capability. Shaft radius is exclusively
        # collision/aperture geometry and must never inflate target tolerance.
        reach_radius = instrument.tip_working_radius_mm + config.target_tolerance_mm
        geometric_candidates = np.flatnonzero(
            (axial >= -TOL)
            & (axial <= instrument.length_mm + TOL)
            & (radial <= reach_radius + TOL)
        ).tolist()
        reached: list[int] = []
        insertion_depths: list[float] = []
        witness_clearances: list[float] = []
        saw_out_of_fov = False
        for index in geometric_candidates:
            _check_cancel(cancel_check)
            depth = max(0.0, float(axial[index]))
            endpoint = entry + direction * depth
            clearance, out_of_fov = _capsule_clearance(
                prepared, entry, endpoint, collision_radius, cancel_check=cancel_check
            )
            if out_of_fov:
                saw_out_of_fov = True
                continue
            if clearance > TOL:
                reached.append(index)
                insertion_depths.append(depth)
                witness_clearances.append(clearance)
        has_out_of_fov |= saw_out_of_fov
        if not reached:
            return TrajectoryResult(
                **common,
                feasible=False,
                reason=(
                    "protected_mask_out_of_fov" if saw_out_of_fov else
                    "protected_structure_collision"
                    if geometric_candidates else "target_not_reached"
                ),
            )
        minimum_clearance = min(witness_clearances, default=math.inf)
        return TrajectoryResult(
            **common,
            feasible=True,
            reached_point_indices=reached,
            insertion_depths_mm=insertion_depths,
            working_depth_mm=float(max(insertion_depths)),
            insertion_span_mm=float(max(insertion_depths) - min(insertion_depths)),
            minimum_clearance_mm=None if math.isinf(minimum_clearance) else minimum_clearance,
        )

    def add(direction, entry, source):
        nonlocal best
        key = tuple(np.round(np.concatenate((direction, entry)), 12))
        if key in seen:
            return None
        result = evaluate(direction, entry, source)
        seen[key] = result
        trajectories.append(result)
        if not result.feasible:
            return result
        reached_union.update(result.reached_point_indices)
        score = (
            len(result.reached_point_indices),
            result.minimum_clearance_mm if result.minimum_clearance_mm is not None else math.inf,
            result.working_depth_mm or 0.0,
        )
        if best is None:
            best = result
        else:
            best_score = (
                len(best.reached_point_indices),
                best.minimum_clearance_mm if best.minimum_clearance_mm is not None else math.inf,
                best.working_depth_mm or 0.0,
            )
            if score > best_score:
                best = result
        return result

    for direction in directions:
        add(direction, portal, "angular_grid")
    grid_results = list(trajectories)
    entries = [portal]
    if config.sampling.portal_offset_rings:
        u, v = orthonormal_basis(normal)
        usable = max(0., config.portal.radius_mm - collision_radius)
        for ring in range(1, config.sampling.portal_offset_rings + 1):
            radius = usable * ring / (config.sampling.portal_offset_rings + 1)
            for az in range(config.sampling.portal_offset_azimuth_steps):
                phi = 2 * math.pi * az / config.sampling.portal_offset_azimuth_steps
                entries.append(portal + radius * (math.cos(phi) * u + math.sin(phi) * v))
    target_ids = np.unique(np.linspace(
        0, len(target) - 1, min(len(target), config.sampling.max_target_witnesses), dtype=int
    ))
    seeds = []
    for entry in entries:
        _check_cancel(cancel_check)
        if not np.array_equal(entry, portal):
            for direction in directions:
                add(direction, entry, "offset_angular_grid")
        if config.sampling.target_directed:
            for index in target_ids:
                vector = target[index] - entry
                length = float(np.linalg.norm(vector))
                if length <= TOL:
                    continue
                direction = vector / length
                if angle_degrees(direction, nominal) <= config.sampling.max_angle_deg + TOL:
                    result = add(direction, entry, "target_directed")
                    if result is None:
                        key = tuple(np.round(np.concatenate((direction, entry)), 12))
                        result = seen[key]
                    seeds.append((direction, entry, result.feasible))
    # Local angular refinement follows target witnesses and mixed feasible/
    # blocked neighborhoods. It is budgeted, not an exhaustive configuration search.
    seeds.extend((np.asarray(t.direction), portal, t.feasible) for t in grid_results
                 if t.feasible)
    budget = config.sampling.max_refinement_directions
    for level in range(config.sampling.adaptive_levels):
        next_seeds = []
        step = math.radians(config.sampling.max_angle_deg) / (
            max(2, config.sampling.polar_steps) * 2 ** (level + 1)
        )
        for direction, entry, parent_feasible in seeds:
            _check_cancel(cancel_check)
            if budget <= 0:
                break
            u, v = orthonormal_basis(direction)
            neighborhood = []
            for tangent in (u, -u, v, -v):
                if budget <= 0:
                    break
                refined = math.cos(step) * direction + math.sin(step) * tangent
                if angle_degrees(refined, nominal) > config.sampling.max_angle_deg + TOL:
                    continue
                result = add(refined, entry, "adaptive")
                if result is not None:
                    budget -= 1
                    neighborhood.append((refined, entry, result.feasible))
            if any(item[2] != parent_feasible for item in neighborhood):
                next_seeds.extend(neighborhood)
                next_seeds.append((direction, entry, parent_feasible))
        seeds = next_seeds
        if not seeds or budget <= 0:
            break

    direction_weights = spherical_cap_sample_weights(
        config.sampling.max_angle_deg,
        config.sampling.polar_steps,
        config.sampling.azimuth_steps,
    )
    feasible_count = sum(t.feasible for t in trajectories)
    polar, azimuth = (
        _direction_components(
            np.asarray(best.direction), np.asarray(config.nominal_direction)
        )
        if best else (None, None)
    )
    incomplete = bool(labels) or has_out_of_fov
    solid_angle = None
    angular_note = "Solid angle unavailable for incomplete anatomy or degenerate angular grids."
    if (not incomplete and config.sampling.polar_steps > 1
            and config.sampling.azimuth_steps >= 3):
        if not case.protected_structures:
            # analysis.__init__ imports convergence/engine; defer to avoid a cycle.
            from skullbase_corridor.analysis.geometric_angular import integrate_unobstructed_solid_angle

            angular = integrate_unobstructed_solid_angle(
                target - portal, axis=nominal,
                half_angle_deg=config.sampling.max_angle_deg,
                portal_normal=normal, portal_radius_mm=config.portal.radius_mm,
                collision_radius_mm=collision_radius,
                reach_radius_mm=instrument.tip_working_radius_mm + config.target_tolerance_mm,
                instrument_length_mm=instrument.length_mm,
                cancel_check=lambda: _check_cancel(cancel_check),
            )
            solid_angle = angular.solid_angle_sr
            angular_note = (
                "Solid angle uses geometry-aware center-entry interval-union integration "
                "for unobstructed targets; independent of witness sampling. "
                f"Termination={angular.termination_reason}; evaluations={angular.evaluations}; "
                f"estimated_error_sr={angular.estimated_error_sr}. "
                "Numerical error estimate is not a certified bound; not anatomical validation."
            )
            if solid_angle is None:
                angular_note += " Measurement suppressed; no fallback to an unvalidated estimate."
        else:
            solid_angle = float(np.sum(direction_weights[[t.feasible for t in grid_results]]))
            angular_note = (
                "Solid angle is coarse center-entry grid quadrature with protected structures; "
                "1% accuracy is NOT established and narrow feasible components may be missed."
            )
    status = (
        (AnalysisStatus.INCOMPLETE if config.allow_unknown_anatomy else AnalysisStatus.ABSTAINED)
        if incomplete
        else AnalysisStatus.COMPLETE
        if best
        else AnalysisStatus.NO_FEASIBLE_TRAJECTORY
    )
    notes = [
        "Geometric model output; not a safe-resection estimate.",
        "Finite angular/entry sampling is not complete: no path found is not certified infeasibility.",
        "Target-directed witnesses and adaptive refinement have zero quadrature weight.",
        angular_note,
        "Working depth is maximum reached axial insertion depth; insertion span is reported separately.",
        "Collision checks cover straight insertion from the opening, not external shaft/handle access.",
        "Tip working reach uses a conservative swept spherical envelope; no articulated-tip motion.",
        "Completeness is relative to declared anatomy only; omitted structures cannot be inferred.",
    ]
    if labels:
        notes.append("Incomplete protected-anatomy knowledge: " + ", ".join(labels))
    if len(target_ids) < len(target):
        notes.append("Target-directed witness budget subsamples target points deterministically.")
    if budget <= 0 and config.sampling.adaptive_levels:
        notes.append("Adaptive angular refinement exhausted its configured direction budget.")
    if config.sampling.portal_offset_rings:
        notes.append("Finite portal offsets use a conservative circular footprint bound.")
    else:
        notes.append("Portal entry sampling is center-only; finite opening positions are unresolved.")
    if has_out_of_fov:
        notes.append(
            "Protected-mask coverage was insufficient for at least one insertion path."
        )
    return ApproachResult(
        name=config.name,
        kind=config.kind,
        status=status,
        target_count=len(target),
        reached_point_indices=sorted(reached_union) if not incomplete else [],
        reached_measure_mm3=(
            len(reached_union) * case.target.point_volume_mm3
            if case.target.point_volume_mm3 is not None and not incomplete else None
        ),
        feasible_trajectory_count=feasible_count if not incomplete else 0,
        feasible_solid_angle_sr=solid_angle,
        witness_direction=best.direction if best and not incomplete else None,
        witness_polar_angle_deg=polar if not incomplete else None,
        witness_azimuth_deg=azimuth if not incomplete else None,
        best_working_depth_mm=best.working_depth_mm if best and not incomplete else None,
        best_insertion_span_mm=best.insertion_span_mm if best and not incomplete else None,
        witness_entry_point_mm=best.entry_point_mm if best and not incomplete else None,
        minimum_clearance_mm=best.minimum_clearance_mm if best and not incomplete else None,
        trajectories=[
            t.model_copy(update={"conditional": True, "minimum_clearance_mm": None})
            if incomplete else t for t in trajectories
        ],
        notes=notes,
    )


def evaluate_exact_path(
    case: CorridorCase,
    candidate: ExactPathCandidate,
    *,
    base_directory: Path | None = None,
    cancel_check: CancelCheck | None = None,
) -> ExactPathResult:
    """Evaluate one supplied finite shaft without searching or sampling."""
    case = CorridorCase.model_validate(case.model_dump())
    candidate = ExactPathCandidate.model_validate(candidate.model_dump())
    _check_cancel(cancel_check)
    common = dict(
        candidate_id=candidate.candidate_id,
        approach_name=candidate.approach_name,
        entry_point_mm=candidate.entry_point_mm,
        target_point_mm=candidate.target_point_mm,
        tip_point_mm=candidate.target_point_mm,
    )
    config = next(
        (item for item in case.approaches if item.name == candidate.approach_name), None
    )
    if config is None:
        return ExactPathResult(
            **common, state=ExactPathState.INVALID, reason="approach_not_found"
        )
    entry = np.asarray(candidate.entry_point_mm, dtype=float)
    tip = np.asarray(candidate.target_point_mm, dtype=float)
    delta = tip - entry
    depth = float(np.linalg.norm(delta))
    if depth <= TOL:
        return ExactPathResult(
            **common, state=ExactPathState.INVALID, depth_mm=depth,
            reason="entry_and_target_coincident",
        )
    direction = delta / depth
    direction_tuple = tuple(float(value) for value in direction)
    prepared = _prepare_geometry(
        case, config, base_directory=base_directory, cancel_check=cancel_check
    )
    unknown = prepared.unknown_anatomy + prepared.unavailable
    assumptions = prepared.removal_assumptions
    radius = max(
        config.instrument.radius_mm, config.instrument.tip_working_radius_mm
    )
    portal_center = np.asarray(config.portal.center_mm)
    portal_normal = np.asarray(config.portal.normal)
    offset = entry - portal_center
    aperture_radius = config.portal.radius_mm - float(np.linalg.norm(offset))
    cosine = float(direction @ portal_normal)
    aperture_clearance = (
        aperture_radius - radius / cosine if cosine > TOL else -math.inf
    )
    aperture_ok = portal_allows_direction(
        direction, portal_normal, aperture_radius, radius
    )
    result_common = dict(
        **common,
        direction=direction_tuple,
        depth_mm=depth,
        aperture_satisfied=aperture_ok,
        aperture_clearance_mm=(
            aperture_clearance if math.isfinite(aperture_clearance) else None
        ),
        unknown_anatomy=unknown,
        required_removal_assumptions=assumptions,
    )
    if not aperture_ok:
        return ExactPathResult(
            **result_common, state=ExactPathState.BLOCKED, reason="portal_aperture"
        )
    if angle_degrees(direction, np.asarray(config.nominal_direction)) > (
        config.sampling.max_angle_deg + TOL
    ):
        return ExactPathResult(
            **result_common, state=ExactPathState.BLOCKED,
            reason="outside_approach_angle",
        )
    if depth > config.instrument.length_mm + TOL:
        return ExactPathResult(
            **result_common, state=ExactPathState.BLOCKED,
            reason="instrument_length_exceeded",
        )
    clearance, out_of_fov = _capsule_clearance(
        prepared, entry, tip, radius, cancel_check=cancel_check
    )
    if clearance <= TOL:
        return ExactPathResult(
            **result_common, state=ExactPathState.BLOCKED,
            minimum_clearance_mm=clearance,
            reason="protected_structure_collision",
        )
    if out_of_fov:
        return ExactPathResult(
            **{**result_common, "unknown_anatomy": (
                unknown + [f"{name}:out_of_fov" for name in out_of_fov]
            )},
            state=ExactPathState.UNAVAILABLE,
            reason="protected_mask_out_of_fov",
        )
    minimum_clearance = None if math.isinf(clearance) else clearance
    if prepared.unavailable or (prepared.unknown_anatomy and not config.allow_unknown_anatomy):
        return ExactPathResult(
            **result_common, state=ExactPathState.UNAVAILABLE,
            minimum_clearance_mm=minimum_clearance,
            reason="protected_anatomy_unavailable",
        )
    if prepared.unknown_anatomy:
        return ExactPathResult(
            **result_common, state=ExactPathState.CONDITIONAL,
            minimum_clearance_mm=minimum_clearance,
            reason="unknown_anatomy_allowed",
        )
    return ExactPathResult(
        **result_common, state=ExactPathState.MODEL_FEASIBLE,
        minimum_clearance_mm=minimum_clearance,
    )


def evaluate_exact_paths(
    case: CorridorCase,
    candidates: list[ExactPathCandidate],
    *,
    base_directory: Path | None = None,
    cancel_check: CancelCheck | None = None,
) -> list[ExactPathResult]:
    """Evaluate supplied paths independently; no candidate generation is performed."""
    return [
        evaluate_exact_path(
            case, candidate, base_directory=base_directory, cancel_check=cancel_check
        )
        for candidate in candidates
    ]


def _combination(first: ApproachResult, second: ApproachResult) -> CombinationResult:
    unknown = {AnalysisStatus.ABSTAINED, AnalysisStatus.INCOMPLETE, AnalysisStatus.UNSUPPORTED}
    if first.status in unknown or second.status in unknown:
        return CombinationResult(
            first=first.name, second=second.name, status=AnalysisStatus.INCOMPLETE,
            union_indices=[], intersection_indices=[], incremental_first_indices=[],
            incremental_second_indices=[],
            notes=["Coverage unavailable: at least one approach has incomplete anatomy/model knowledge."],
        )
    a, b = set(first.reached_point_indices), set(second.reached_point_indices)
    return CombinationResult(
        first=first.name,
        second=second.name,
        union_indices=sorted(a | b),
        intersection_indices=sorted(a & b),
        incremental_first_indices=sorted(a - b),
        incremental_second_indices=sorted(b - a),
        notes=["Set operations on sampled reachability only; unobserved paths remain unresolved.",
               "Union coverage does not establish simultaneous instrument feasibility."],
    )


def analyze_simultaneous_pair(
    first_config: ApproachConfig,
    first_result: ApproachResult,
    second_config: ApproachConfig,
    second_result: ApproachResult,
    *,
    minimum_angle_deg: float,
    cancel_check: CancelCheck | None = None,
) -> SimultaneousPairResult:
    _check_cancel(cancel_check)
    first_config = ApproachConfig.model_validate(first_config.model_dump())
    second_config = ApproachConfig.model_validate(second_config.model_dump())
    if first_result.name != first_config.name or second_result.name != second_config.name:
        raise ValueError("pair results must match their approach configurations")
    if not math.isfinite(minimum_angle_deg) or not 0 <= minimum_angle_deg <= 180:
        raise ValueError("minimum angle must be finite and between 0 and 180 degrees")
    common = dict(first=first_config.name, second=second_config.name,
                  minimum_angle_deg=minimum_angle_deg)
    unknown = {AnalysisStatus.ABSTAINED, AnalysisStatus.INCOMPLETE, AnalysisStatus.UNSUPPORTED}
    if (first_result.status in unknown or second_result.status in unknown
            or any(t.conditional for t in first_result.trajectories + second_result.trajectories)):
        return SimultaneousPairResult(
            **common, feasible=None, status=AnalysisStatus.INCOMPLETE,
            notes=["Simultaneous feasibility unavailable because an approach is incomplete."],
        )
    if first_config.shared_portal_overlap_mm or second_config.shared_portal_overlap_mm:
        return SimultaneousPairResult(
            **common, feasible=None, status=AnalysisStatus.UNSUPPORTED,
            notes=["Ignoring shared-portal shaft overlap is physically unsupported."],
        )
    first_feasible = [t for t in first_result.trajectories if t.feasible]
    second_feasible = [t for t in second_result.trajectories if t.feasible]
    best: tuple[float, float, TrajectoryResult, TrajectoryResult] | None = None
    for first, second in ((a, b) for a in first_feasible for b in second_feasible):
        _check_cancel(cancel_check)
        d1, d2 = np.asarray(first.direction), np.asarray(second.direction)
        angle = angle_degrees(d1, d2)
        if angle + TOL < minimum_angle_deg:
            continue
        p1 = np.asarray(first.entry_point_mm or first_config.portal.center_mm)
        p2 = np.asarray(second.entry_point_mm or second_config.portal.center_mm)
        depth1 = max(first.insertion_depths_mm)
        depth2 = max(second.insertion_depths_mm)
        clearance = capsule_capsule_clearance(
            p1, p1 + d1 * depth1,
            max(first_config.instrument.radius_mm, first_config.instrument.tip_working_radius_mm),
            p2, p2 + d2 * depth2,
            max(second_config.instrument.radius_mm, second_config.instrument.tip_working_radius_mm),
        )
        # With no declared overlap, same-base shafts collide at their common
        # portal even when collinear or immediately divergent.
        if clearance <= TOL:
            continue
        candidate = (clearance, angle, first, second)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return SimultaneousPairResult(
        first=first_config.name,
        second=second_config.name,
        feasible=best is not None,
        status=AnalysisStatus.COMPLETE if best else AnalysisStatus.NO_FEASIBLE_TRAJECTORY,
        minimum_angle_deg=minimum_angle_deg,
        actual_angle_deg=best[1] if best else None,
        shaft_clearance_mm=best[0] if best else None,
        first_direction=best[2].direction if best else None,
        second_direction=best[3].direction if best else None,
        first_entry_point_mm=best[2].entry_point_mm if best else None,
        second_entry_point_mm=best[3].entry_point_mm if best else None,
        first_reached_point_indices=best[2].reached_point_indices if best else [],
        second_reached_point_indices=best[3].reached_point_indices if best else [],
        notes=["Sampled simultaneous straight-insertion envelopes only; no handles or external shafts.",
               "Each direction is tested at its deepest reached insertion; shallower pairs may be missed.",
               "No sampled pair found does not certify pair infeasibility."],
    )


def analyze_case(
    case: CorridorCase,
    *,
    simultaneous_minimum_angle_deg: float | None = None,
    base_directory: Path | None = None,
    cancel_check: CancelCheck | None = None,
) -> CaseResult:
    _check_cancel(cancel_check)
    if simultaneous_minimum_angle_deg is not None and (
        not math.isfinite(simultaneous_minimum_angle_deg)
        or not 0 <= simultaneous_minimum_angle_deg <= 180
    ):
        raise ValueError("minimum angle must be finite and between 0 and 180 degrees")
    case = CorridorCase.model_validate(case.model_dump())
    results = [
        analyze_approach(case, config, base_directory=base_directory, cancel_check=cancel_check)
        for config in case.approaches
    ]
    combos = []
    for a, b in combinations(results, 2):
        _check_cancel(cancel_check)
        combos.append(_combination(a, b))
    pairs: list[SimultaneousPairResult] = []
    if simultaneous_minimum_angle_deg is not None:
        by_name = {config.name: config for config in case.approaches}
        pairs = [
            analyze_simultaneous_pair(
                by_name[a.name], a, by_name[b.name], b,
                minimum_angle_deg=simultaneous_minimum_angle_deg,
                cancel_check=cancel_check,
            )
            for a, b in combinations(results, 2)
        ]
    _check_cancel(cancel_check)
    return CaseResult(
        case_id=case.case_id,
        approaches=results,
        combinations=combos,
        simultaneous_pairs=pairs,
    )
