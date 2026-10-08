"""Conservative, sampled EEA/TM coverage comparison; never a surgery recommendation.

Only imports numerical/domain code, so renderers and batch exporters can use this
module without Qt. Results are not changed or re-run.
"""

from __future__ import annotations

import csv
import os
import tempfile
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict

from corridorkit.domain.models import (
    AnalysisStatus, ApproachKind, ApproachResult, CaseResult, CorridorCase,
    SampledMaskTarget,
)
from corridorkit.export.json import atomic_json_write, checksum


class CoverageCategory(StrEnum):
    EEA_ONLY = "eea_only"
    TM_ONLY = "tm_only"
    BOTH = "both"
    NOT_REACHED = "not_reached"
    UNAVAILABLE = "unavailable"


CATEGORY_LABELS = {
    CoverageCategory.EEA_ONLY: "EEA only (sampled)",
    CoverageCategory.TM_ONLY: "TM only (sampled)",
    CoverageCategory.BOTH: "Both (sampled)",
    CoverageCategory.NOT_REACHED: "Not reached at this sampling",
    CoverageCategory.UNAVAILABLE: "Unavailable",
}

SAMPLING_CAVEAT = (
    "Finite direction/entry sampling: no path found is not proof of no possible path. "
    "Coverage is the union across configured portals of each kind, not simultaneous "
    "instrument feasibility. These geometric results do not establish approach "
    "necessity, recommend surgery, or predict safe resection."
)


class CoverageComparison(BaseModel):
    """One category per target point, preserving the case's original point order.

    A null membership means unknown, not false. Counts include all five categories.
    TM-only is a lower bound on incremental sampled coverage when unavailable > 0.
    Volume is partitioned by category only when full mask-cell weights are valid;
    unavailable volume is unclassified volume, never a no-reach estimate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    case_id: str
    target_count: int
    categories: list[CoverageCategory]
    counts: dict[str, int]
    eea_reached: list[bool | None]
    tm_reached: list[bool | None]
    volume_mm3: dict[str, float] | None
    point_volume_mm3: float | None
    volume_explanation: str
    tm_incremental_count: int
    tm_incremental_complete: bool
    tm_incremental_volume_mm3: float | None
    explanation: str
    issues: list[str]

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _valid_indices(indices: list[int], count: int) -> bool:
    # model_copy/model_construct bypass domain validation; booleans are not indices.
    return all(type(index) is int and 0 <= index < count for index in indices)


def _approach_error(item: ApproachResult, count: int) -> str | None:
    if type(item.target_count) is not int or item.target_count != count:
        return "target count does not match the current case"
    if not _valid_indices(item.reached_point_indices, count):
        return "invalid reached-point indices"
    if len(set(item.reached_point_indices)) != len(item.reached_point_indices):
        return "duplicate reached-point indices"
    if item.status not in (AnalysisStatus.COMPLETE, AnalysisStatus.NO_FEASIBLE_TRAJECTORY):
        return f"status {item.status} does not establish sampled coverage"
    if any(t.conditional for t in item.trajectories):
        return "conditional trajectories do not establish unconditional coverage"
    if any(not _valid_indices(t.reached_point_indices, count) for t in item.trajectories):
        return "invalid trajectory target indices"
    if type(item.feasible_trajectory_count) is not int or item.feasible_trajectory_count < 0:
        return "invalid feasible trajectory count"
    if item.status == AnalysisStatus.COMPLETE and (
        not item.reached_point_indices or not item.feasible_trajectory_count
    ):
        return "complete status contradicts missing feasible reach"
    if item.status == AnalysisStatus.NO_FEASIBLE_TRAJECTORY and (
        item.reached_point_indices or item.feasible_trajectory_count
        or any(t.feasible for t in item.trajectories)
    ):
        return "no-feasible-trajectory status contradicts reported reach"
    if item.trajectories:
        witnessed = {i for t in item.trajectories if t.feasible for i in t.reached_point_indices}
        if witnessed != set(item.reached_point_indices):
            return "aggregate reach disagrees with feasible trajectories"
        if sum(t.feasible for t in item.trajectories) != item.feasible_trajectory_count:
            return "feasible trajectory count disagrees with trajectories"
    return None


def _volume_weight(case: CorridorCase) -> tuple[float | None, str]:
    target = case.target
    if not isinstance(target, SampledMaskTarget) or target.source != "mask_voxel_centers":
        return None, "Counts only: explicit points or sparse/subsampled targets are not volume estimates."
    if target.point_volume_mm3 is None:
        return None, "Counts only: full-mask physical point weights are missing."
    affine = np.asarray(target.affine, dtype=float)
    weight = target.point_volume_mm3
    determinant = abs(float(np.linalg.det(affine[:3, :3])))
    if not (
        np.isfinite(weight) and weight > 0
        and np.isclose(weight, target.voxel_volume_mm3, rtol=1e-6, atol=1e-12)
        and np.isclose(weight, determinant, rtol=1e-6, atol=1e-12)
        and np.isfinite(weight * len(target.points_mm))
    ):
        return None, "Counts only: inconsistent or invalid physical voxel weights."
    points = np.asarray(target.points_mm, dtype=float)
    indices = np.linalg.solve(affine[:3, :3], (points - affine[:3, 3]).T).T
    rounded = np.rint(indices)
    if (
        not np.allclose(indices, rounded, rtol=0, atol=1e-5)
        or np.any(rounded < 0)
        or np.any(rounded >= np.asarray(target.source_shape))
        or len(np.unique(rounded, axis=0)) != len(points)
    ):
        return None, "Counts only: target points are not unique in-grid voxel centers."
    return float(weight), (
        "Volume uses declared full mask voxel centers and affine-consistent cell weights "
        "(mm³); source-mask completeness is declared, not independently verified here."
    )


def build_coverage_comparison(
    case: CorridorCase, result: CaseResult | None,
) -> CoverageComparison:
    """Compare validated per-kind unions using three-valued membership.

    A valid portal's positive reach remains evidence even if another same-kind
    portal is unavailable. Absence requires valid results for *all* configured
    portals of that kind. An absent kind is unavailable, not an empty union.
    CaseResult lacks a configuration hash: same-ID stale geometry cannot be
    detected here; callers must invalidate results whenever configuration changes.
    """
    count = len(case.target.points_mm)
    issues: list[str] = []
    by_name: dict[str, list[ApproachResult]] = {}
    if result is None:
        issues.append("No result is available.")
    elif result.case_id != case.case_id:
        issues.append("Result case ID does not match the current case; all coverage is unavailable.")
    else:
        for item in result.approaches:
            by_name.setdefault(item.name, []).append(item)
        extras = set(by_name) - {a.name for a in case.approaches}
        if extras:
            issues.append("Unexpected approach results: " + ", ".join(sorted(extras)))
            # Unexpected configurations cannot be safely attributed to this case.
            by_name.clear()

    memberships: dict[ApproachKind, list[bool | None]] = {}
    for kind in (ApproachKind.EEA, ApproachKind.TRANSMAXILLARY):
        configured = [a for a in case.approaches if a.kind == kind]
        reached: set[int] = set()
        complete = bool(configured)
        if not configured:
            issues.append(f"No {kind.value} approach is configured.")
        for config in configured:
            candidates = by_name.get(config.name, [])
            error = None
            if len(candidates) != 1:
                error = "missing result" if not candidates else "duplicate approach results"
            elif candidates[0].kind != config.kind:
                error = "approach kind does not match the current case"
            else:
                error = _approach_error(candidates[0], count)
            if error:
                complete = False
                issues.append(f"{config.name}: {error}.")
            else:
                reached.update(candidates[0].reached_point_indices)
        memberships[kind] = [
            True if i in reached else False if complete else None for i in range(count)
        ]
    eea = memberships[ApproachKind.EEA]
    tm = memberships[ApproachKind.TRANSMAXILLARY]
    categories = []
    for first, second in zip(eea, tm):
        if first is None or second is None:
            category = CoverageCategory.UNAVAILABLE
        elif first and second:
            category = CoverageCategory.BOTH
        elif first:
            category = CoverageCategory.EEA_ONLY
        elif second:
            category = CoverageCategory.TM_ONLY
        else:
            category = CoverageCategory.NOT_REACHED
        categories.append(category)
    tally = Counter(categories)
    counts = {category.value: tally[category] for category in CoverageCategory}
    weight, volume_explanation = _volume_weight(case)
    volume = {key: value * weight for key, value in counts.items()} if weight is not None else None
    incremental = counts["tm_only"]
    complete = not counts["unavailable"]
    qualifier = "" if complete else "at least "
    explanation = (
        f"TM incremental sampled coverage over EEA: {qualifier}{incremental} of {count} "
        "target samples (TM-only). "
        + (f"{counts['unavailable']} samples have unavailable comparisons; the total increment "
           "cannot be determined. " if not complete else "")
        + volume_explanation + " " + SAMPLING_CAVEAT
    )
    return CoverageComparison(
        case_id=case.case_id, target_count=count, categories=categories, counts=counts,
        eea_reached=eea, tm_reached=tm, volume_mm3=volume,
        point_volume_mm3=weight, volume_explanation=volume_explanation,
        tm_incremental_count=incremental, tm_incremental_complete=complete,
        tm_incremental_volume_mm3=volume["tm_only"] if volume is not None else None,
        explanation=explanation, issues=issues,
    )


def export_comparison(
    directory: str | Path, case: CorridorCase, result: CaseResult | None,
) -> dict[str, Path]:
    """Write comparison.json and comparison_points.csv, atomically per file.

    The JSON includes current input/result hashes for provenance, not proof that a
    same-ID result was computed from the supplied configuration.
    """
    if result is not None and result.case_id != case.case_id:
        raise ValueError("result belongs to a different case")
    comparison = build_coverage_comparison(case, result)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {"json": directory / "comparison.json", "csv": directory / "comparison_points.csv"}
    envelope = comparison.to_dict()
    envelope.update({
        "comparison_schema_version": "1.0",
        "case_sha256": checksum(case.model_dump(mode="json")),
        "result_sha256": checksum(result.model_dump(mode="json")) if result is not None else None,
        "coordinate_frame": case.coordinate_frame,
        "provenance_note": "Hashes identify supplied inputs; CaseResult has no configuration hash.",
    })
    fd, temporary = tempfile.mkstemp(prefix=".corridor-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow((
                "point_index", "x_mm", "y_mm", "z_mm", "coordinate_frame", "category",
                "eea_sampled_reached", "tm_sampled_reached", "point_volume_mm3",
            ))
            for index, point in enumerate(case.target.points_mm):
                writer.writerow((
                    index, *point, case.coordinate_frame, comparison.categories[index].value,
                    comparison.eea_reached[index], comparison.tm_reached[index],
                    comparison.point_volume_mm3,
                ))
            stream.flush()
            os.fsync(stream.fileno())
        atomic_json_write(paths["json"], envelope)
        os.replace(temporary, paths["csv"])
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return paths
