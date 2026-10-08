"""Coverage is a sampled partition, not a clinical or exhaustive reachability claim."""

import csv
import importlib.util
import json
import os

import numpy as np
import pytest

from corridorkit.analysis.coverage import (
    CoverageCategory, build_coverage_comparison, export_comparison,
)
from corridorkit.domain.models import (
    AnalysisStatus, ApproachConfig, ApproachKind, ApproachResult, CaseResult,
    CorridorCase, PortalDisk, RigidInstrument, SamplingConfig, TargetPointCloud,
    TrajectoryResult,
)
from corridorkit.geometry.engine import analyze_case
from corridorkit.io.masks import target_from_mask

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def make_case(kinds=("eea", "transmaxillary"), target=None):
    return CorridorCase(
        case_id="coverage",
        target=target if target is not None else TargetPointCloud(
            points_mm=[(float(i), 0, 5) for i in range(6)],
        ),
        approaches=[
            ApproachConfig(
                name=f"portal-{i}", kind=kind,
                portal=PortalDisk(center_mm=(0, 0, 0), normal=(0, 0, 1), radius_mm=2),
                nominal_direction=(0, 0, 1),
                instrument=RigidInstrument(length_mm=10, radius_mm=.1),
                sampling=SamplingConfig(
                    polar_steps=1, azimuth_steps=1, target_directed=False, adaptive_levels=0,
                ),
            ) for i, kind in enumerate(kinds)
        ],
    )


def make_result(case, reaches, statuses=None):
    statuses = statuses or [AnalysisStatus.COMPLETE if ids else AnalysisStatus.NO_FEASIBLE_TRAJECTORY
                            for ids in reaches]
    return CaseResult(
        case_id=case.case_id,
        approaches=[
            ApproachResult(
                name=config.name, kind=config.kind, status=status,
                target_count=len(case.target.points_mm), reached_point_indices=list(ids),
                reached_measure_mm3=None, feasible_trajectory_count=1 if ids else 0,
                feasible_solid_angle_sr=None, witness_direction=None,
                best_working_depth_mm=None, minimum_clearance_mm=None, trajectories=[],
            )
            for config, ids, status in zip(case.approaches, reaches, statuses)
        ],
    )


def replace_approach(result, index=0, **changes):
    approaches = list(result.approaches)
    approaches[index] = approaches[index].model_copy(update=changes)
    return result.model_copy(update={"approaches": approaches})


def test_multi_portal_union_overlaps_and_ordered_partition():
    case = make_case(("eea", "transmaxillary", "eea", "transmaxillary"))
    result = make_result(case, [[0, 1], [1, 2], [1, 3], [2, 4]])
    before = result.model_dump_json()
    comparison = build_coverage_comparison(case, result)
    assert comparison.categories == [
        "eea_only", "both", "tm_only", "eea_only", "tm_only", "not_reached",
    ]
    assert comparison.counts == {
        "eea_only": 2, "tm_only": 2, "both": 1, "not_reached": 1, "unavailable": 0,
    }
    assert sum(comparison.counts.values()) == len(case.target.points_mm)
    assert comparison.tm_incremental_count == 2
    assert comparison.tm_incremental_complete
    assert comparison.volume_mm3 is None
    assert "no path found is not proof" in comparison.explanation
    assert "necessity" in comparison.explanation and "safe resection" in comparison.explanation
    assert result.model_dump_json() == before
    json.dumps(comparison.to_dict(), allow_nan=False)


def test_missing_result_is_unavailable_not_zero_reach():
    case = make_case()
    comparison = build_coverage_comparison(case, None)
    assert comparison.categories == ["unavailable"] * 6
    assert comparison.eea_reached == [None] * 6
    assert comparison.counts["not_reached"] == 0
    assert not comparison.tm_incremental_complete
    assert "cannot be determined" in comparison.explanation


@pytest.mark.parametrize("status", [
    AnalysisStatus.ABSTAINED, AnalysisStatus.INCOMPLETE, AnalysisStatus.UNSUPPORTED, "invalid",
])
def test_unavailable_status_never_means_not_reached(status):
    case = make_case()
    result = replace_approach(make_result(case, [[0], [0, 1]]), status=status)
    comparison = build_coverage_comparison(case, result)
    assert comparison.categories == ["unavailable"] * 6
    assert comparison.eea_reached == [None] * 6
    assert comparison.counts["tm_only"] == 0


def test_unknown_same_kind_portal_preserves_positive_but_not_negative_evidence():
    case = make_case(("eea", "eea", "transmaxillary"))
    result = make_result(case, [[0, 1], [], [1, 2]])
    result = replace_approach(result, 1, status=AnalysisStatus.INCOMPLETE)
    comparison = build_coverage_comparison(case, result)
    assert comparison.categories == ["eea_only", "both"] + ["unavailable"] * 4
    assert comparison.eea_reached == [True, True, None, None, None, None]
    assert comparison.tm_incremental_count == 0


def test_known_increment_is_lower_bound_with_unknown_extra_tm_portal():
    case = make_case(("eea", "transmaxillary", "transmaxillary"))
    result = make_result(case, [[0], [1], []])
    result = replace_approach(result, 2, status=AnalysisStatus.ABSTAINED)
    comparison = build_coverage_comparison(case, result)
    assert comparison.categories[1] == "tm_only"
    assert comparison.tm_incremental_count == 1
    assert "at least 1" in comparison.explanation
    assert not comparison.tm_incremental_complete


@pytest.mark.parametrize("indices", [[-1], [6], [True], [1.5], [0, 0]])
def test_invalid_indices_make_entire_approach_unavailable(indices):
    case = make_case()
    result = replace_approach(make_result(case, [[0], [1]]), reached_point_indices=indices)
    comparison = build_coverage_comparison(case, result)
    assert comparison.categories == ["unavailable"] * 6
    assert any("indices" in issue for issue in comparison.issues)


@pytest.mark.parametrize("changes", [
    {"target_count": 7},
    {"kind": ApproachKind.TRANSMAXILLARY},
    {"status": AnalysisStatus.NO_FEASIBLE_TRAJECTORY},
    {"feasible_trajectory_count": -1},
    {"feasible_trajectory_count": 0},
    {"feasible_trajectory_count": True},
    {"reached_point_indices": []},
    {"trajectories": [TrajectoryResult(direction=(0, 0, 1), feasible=True,
                                      reached_point_indices=[0], conditional=True)]},
    {"trajectories": [TrajectoryResult(direction=(0, 0, 1), feasible=True,
                                      reached_point_indices=[6])]},
    {"trajectories": [TrajectoryResult(direction=(0, 0, 1), feasible=True,
                                      reached_point_indices=[1])]},
])
def test_inconsistent_records_are_unavailable(changes):
    case = make_case()
    result = replace_approach(make_result(case, [[0], [1]]), **changes)
    assert build_coverage_comparison(case, result).categories == ["unavailable"] * 6


def test_missing_duplicate_and_unexpected_approach_records():
    case = make_case()
    result = make_result(case, [[0], [1]])
    for approaches in (
        result.approaches[1:],
        [*result.approaches, result.approaches[0]],
        [*result.approaches, result.approaches[0].model_copy(update={"name": "unexpected"})],
    ):
        comparison = build_coverage_comparison(case, result.model_copy(update={"approaches": approaches}))
        assert comparison.categories == ["unavailable"] * 6


def test_no_configured_tm_is_not_an_empty_known_union():
    case = make_case(("eea",))
    comparison = build_coverage_comparison(case, make_result(case, [[0]]))
    assert comparison.categories == ["unavailable"] * 6
    assert comparison.tm_reached == [None] * 6


def test_case_id_mismatch_display_and_export(tmp_path):
    case = make_case()
    result = make_result(case, [[0], [1]]).model_copy(update={"case_id": "other"})
    comparison = build_coverage_comparison(case, result)
    assert comparison.categories == ["unavailable"] * 6
    assert "case ID" in comparison.issues[0]
    with pytest.raises(ValueError, match="different case"):
        export_comparison(tmp_path, case, result)
    assert list(tmp_path.iterdir()) == []


def test_finite_sampling_is_valid_but_not_exhaustive():
    case = make_case()
    result = analyze_case(case)
    assert all(not a.sampling_complete for a in result.approaches)
    comparison = build_coverage_comparison(case, result)
    assert comparison.counts["unavailable"] == 0
    assert comparison.counts["both"] > 0
    assert comparison.counts["not_reached"] > 0


def test_full_mask_volume_partitions_by_affine_weight():
    affine = np.diag([2., 3., 4., 1.])
    target = target_from_mask(np.ones((2, 3, 1)), affine)
    case = make_case(target=target)
    result = make_result(case, [[0, 1], [1, 2]])
    # Ignore cached result volumes; derive from validated target cell weights.
    result = replace_approach(result, reached_measure_mm3=999999)
    comparison = build_coverage_comparison(case, result)
    assert comparison.point_volume_mm3 == pytest.approx(24)
    assert comparison.volume_mm3 == pytest.approx({
        "eea_only": 24., "tm_only": 24., "both": 24., "not_reached": 72., "unavailable": 0.,
    })
    assert comparison.tm_incremental_volume_mm3 == pytest.approx(24.)
    assert sum(comparison.volume_mm3.values()) == pytest.approx(144.)


@pytest.mark.parametrize("stride", [2, 4])
def test_sparse_mask_never_fabricates_volume(stride):
    target = target_from_mask(np.ones((2, 3, 1)), np.eye(4), stride=stride)
    # Even a accidentally assigned weight must not override the sparse source.
    target = target.model_copy(update={"point_volume_mm3": 1.})
    case = make_case(target=target)
    comparison = build_coverage_comparison(case, make_result(case, [[0], []]))
    assert comparison.volume_mm3 is None
    assert comparison.point_volume_mm3 is None
    assert comparison.tm_incremental_volume_mm3 is None


def test_point_cloud_weight_does_not_claim_voxel_volume():
    case = make_case(target=TargetPointCloud(points_mm=[(0, 0, 5)], point_volume_mm3=10))
    assert build_coverage_comparison(case, make_result(case, [[0], []])).volume_mm3 is None


@pytest.mark.parametrize("changes", [
    {"point_volume_mm3": None}, {"point_volume_mm3": 2.}, {"voxel_volume_mm3": 2.},
    {"points_mm": [(0., 0., 0.)] * 6},
    {"points_mm": [(0.5, 0., 0.)] * 6},
    {"points_mm": [(7., 0., 0.)] * 6},
])
def test_invalid_mask_weights_or_centers_suppress_volume(changes):
    target = target_from_mask(np.ones((2, 3, 1)), np.eye(4)).model_copy(update=changes)
    case = make_case(target=target)
    assert build_coverage_comparison(case, make_result(case, [[0], [1]])).volume_mm3 is None


def test_unavailable_mask_volume_is_not_reported_as_no_reach():
    target = target_from_mask(np.ones((2, 3, 1)), np.eye(4))
    comparison = build_coverage_comparison(make_case(target=target), None)
    assert comparison.volume_mm3["unavailable"] == 6
    assert comparison.volume_mm3["not_reached"] == 0
    assert not comparison.tm_incremental_complete


def test_exports_json_and_all_point_rows_without_local_paths(tmp_path):
    case = make_case()
    paths = export_comparison(tmp_path / "export", case, make_result(case, [[0, 1], [1, 2]]))
    payload = json.loads(paths["json"].read_text())
    assert payload["categories"] == [
        "eea_only", "both", "tm_only", "not_reached", "not_reached", "not_reached",
    ]
    assert payload["volume_mm3"] is None
    assert len(payload["case_sha256"]) == 64
    with paths["csv"].open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 6
    assert [row["point_index"] for row in rows] == list(map(str, range(6)))
    assert [row["category"] for row in rows] == payload["categories"]
    assert all(row["point_volume_mm3"] == "" for row in rows)
    assert sorted(path.name for path in paths["json"].parent.iterdir()) == [
        "comparison.json", "comparison_points.csv",
    ]


def test_csv_unknown_membership_is_blank_not_false(tmp_path):
    paths = export_comparison(tmp_path, make_case(), None)
    with paths["csv"].open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert all(row["eea_sampled_reached"] == "" and row["tm_sampled_reached"] == ""
               and row["category"] == "unavailable" for row in rows)


@pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="Qt is optional")
def test_panel_case_result_and_clear_lifecycle():
    from PySide6 import QtWidgets
    from corridorkit.desktop.comparison import ComparisonPanel

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = ComparisonPanel()
    case = make_case()
    result = make_result(case, [[0], [1]])
    assert panel.comparison is None
    panel.set_case(case)
    assert panel.comparison.counts["unavailable"] == 6
    panel.set_result(result)
    assert panel.table.rowCount() == len(CoverageCategory)
    assert panel.comparison.tm_incremental_count == 1
    panel.set_case(case)
    assert panel.result is None
    assert panel.comparison.counts["unavailable"] == 6
    panel.set_result(result)
    panel.set_result(None)
    assert panel.comparison.counts["unavailable"] == 6
    panel.clear()
    assert panel.table.rowCount() == 0 and panel.comparison is None
    panel.set_result(result)
    assert panel.result is None
    panel.close()
    app.processEvents()
