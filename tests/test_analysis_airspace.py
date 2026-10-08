import numpy as np
import pytest
from scipy.spatial import cKDTree

from skullbase_corridor.analysis.airspace import (
    accelerated_clearance, contained, evaluate_airspace, reference_clearance,
)


def test_accelerated_clearance_matches_exhaustive_random_segments():
    rng = np.random.default_rng(42)
    centers = rng.uniform(-10, 10, (500, 3))
    tree = cKDTree(centers)
    for _ in range(40):
        start, end = rng.uniform(-8, 8, (2, 3))
        assert accelerated_clearance(start, end, centers, tree, 0.7) == pytest.approx(
            reference_clearance(start, end, centers, 0.7), abs=1e-10
        )


def test_capsule_containment_uses_physical_radius_and_affine():
    affine = np.diag([-0.5, 1, 2, 1])
    assert contained(np.array([-2, 4, 6]), np.array([-3, 5, 8]), affine, (10, 10, 10), 1)
    assert not contained(np.array([0, 4, 6]), np.array([-3, 5, 8]), affine, (10, 10, 10), 1)


def test_synthetic_airspace_has_real_interior_targets_and_finite_length_failures():
    label = np.zeros((20, 20, 20), dtype=np.uint8)
    label[3:17, 3:17, 3:17] = 1
    report = evaluate_airspace(label, np.eye(4))
    assert report["status"] == "evaluated"
    assert report["expert_anatomical_review"] == "incomplete"
    assert report["geometry_comparison"]["false_feasible"] == 0
    assert report["geometry_comparison"]["classification_mismatches"] == 0
    assert all(label[tuple(target["index"])] == target["label"] for target in report["targets"])
    assert any(row["reason"] == "finite_length_exceeded"
               for trajectory in report["trajectories"] for scenario in trajectory["scenarios"]
               for row in scenario["grid"].values())
    assert len(report["counts"]) == 4


def test_airspace_excludes_missing_targets_and_sheared_grid():
    assert evaluate_airspace(np.zeros((4, 4, 4)), np.eye(4))["status"] == "excluded"
    affine = np.eye(4)
    affine[0, 1] = 0.2
    assert evaluate_airspace(np.ones((4, 4, 4)), affine)["exclusion_reasons"] == [
        "nonorthogonal_grid_unsupported"
    ]


def test_reference_and_fast_clearance_at_contact_and_far_cap():
    start, end = np.array([0.0, 0, 0]), np.array([10.0, 0, 0])
    centers = np.array([[5.0, 1.5, 0], [-1.5, 0, 0], [11.5, 0, 0]])
    tree = cKDTree(centers)
    # A 1 mm shaft touching a 0.5 mm boundary sphere has exactly 1 mm clearance.
    assert reference_clearance(start, end, centers, 0.5) == pytest.approx(1)
    assert accelerated_clearance(start, end, centers, tree, 0.5) == pytest.approx(1)
    far = np.array([[100.0, 100, 100]])
    assert reference_clearance(start, end, far, 0.5) == 2
    assert accelerated_clearance(start, end, far, cKDTree(far), 0.5) == 2


def test_segmentation_inflation_never_increases_demonstrated_coverage():
    label = np.zeros((12, 12, 12), dtype=np.uint8)
    label[1:11, 1:11, 1:11] = 1
    angle = np.pi / 6
    affine = np.eye(4)
    affine[:2, :2] = [[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]]
    report = evaluate_airspace(label, affine)
    assert report["geometry_comparison"]["classification_mismatches"] == 0
    baseline = report["conservative_coverage"]["baseline"]
    inflated = report["conservative_coverage"]["segmentation_boundary_plus_1mm"]
    assert all(inflated[key] <= baseline[key] for key in baseline)
