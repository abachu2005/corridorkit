"""Small reproducible CI subset of the separate independent-box benchmark."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

RUNNER = Path(__file__).resolve().parents[1] / "research/run_voxel_refinement.py"
spec = importlib.util.spec_from_file_location("voxel_refinement_evidence", RUNNER)
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


@pytest.mark.parametrize("start,end,distance", [
    ([0, 0, 0], [.1, 0, 0], 0.),
    ([-2, 0, 0], [2, 0, 0], 0.),
    ([1, 0, 0], [1, 0, 0], .5),
    ([1, 1, .5], [1, 1, .5], np.sqrt(.5)),
    ([-2, .5, .5], [2, .5, .5], 0.),
])
def test_independent_box_bounds_cover_analytic_distances(start, end, distance):
    lower, upper = evidence.independent_distance_bounds(
        np.array(start), np.array(end), [[0, 0, 0]], np.eye(4))
    assert lower <= distance + 1e-14
    assert upper >= distance - 1e-14
    assert upper - lower < 1e-10


def test_seeded_affine_refinement_safety_against_independent_box_oracle(tmp_path):
    rows = evidence.synthetic(tmp_path, tmp_path, draws=16)
    result = evidence.summarize(rows)
    assert result["false_clear"] == 0
    assert result["clearance_bound_violations"] == 0
    assert result["positive_lower_bound_unresolved"] == 0
    assert result["max_oracle_gap_mm"] < evidence.TOLERANCE_MM
    assert result["fov_policy_changes"] == result["budget_fallback_changes"] == 0
    assert result["budget_exceeded"] == 5
    assert result["out_of_fov"] == 30
    assert result["recovered_clear"] > 50
    for row in rows:
        if row["kind"] in ("corner_touch", "edge_touch", "interior", "crossing"):
            assert row["optional"]["clearance_mm"] <= 0
