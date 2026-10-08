import math

import numpy as np
import pytest

from skullbase_corridor.domain.models import (
    AnalysisStatus,
    ApproachConfig,
    ApproachKind,
    CorridorCase,
    MeshGeometry,
    PortalDisk,
    ProtectedStructure,
    RigidInstrument,
    SamplingConfig,
    SphereGeometry,
    TargetPointCloud,
    VoxelGeometry,
)
from skullbase_corridor.geometry.engine import analyze_case
from skullbase_corridor.geometry.primitives import (
    capsule_sphere_clearance,
    portal_allows_direction,
    segment_segment_distance,
)
from skullbase_corridor.geometry.reference import (
    sampled_capsule_sphere_clearance,
    sampled_segment_distance,
)


def config(
    name="A",
    *,
    center=(0.0, 0.0, 0.0),
    direction=(0.0, 0.0, 1.0),
    radius=0.5,
    tip_radius=0.0,
    tolerance=0.1,
    overlap=0.0,
):
    return ApproachConfig(
        name=name,
        kind=ApproachKind.EEA,
        portal=PortalDisk(center_mm=center, normal=direction, radius_mm=4.0),
        nominal_direction=direction,
        instrument=RigidInstrument(
            length_mm=20.0,
            radius_mm=radius,
            tip_working_radius_mm=tip_radius,
        ),
        sampling=SamplingConfig(
            max_angle_deg=20.0, polar_steps=1, azimuth_steps=1,
            target_directed=False, adaptive_levels=0,
        ),
        target_tolerance_mm=tolerance,
        shared_portal_overlap_mm=overlap,
    )


def case(targets, approaches, protected=None):
    return CorridorCase(
        case_id="contract",
        target=TargetPointCloud(points_mm=targets),
        protected_structures=protected or [],
        approaches=approaches,
    )


def test_shaft_radius_does_not_inflate_target_reach():
    result = analyze_case(
        case([(1.5, 0, 10)], [config(radius=2.0, tolerance=0.1)])
    ).approaches[0]
    assert result.status == AnalysisStatus.NO_FEASIBLE_TRAJECTORY


def test_tip_working_radius_explicitly_extends_reach():
    result = analyze_case(
        case([(1.5, 0, 10)], [config(radius=0.2, tip_radius=1.5, tolerance=0)])
    ).approaches[0]
    assert result.reached_point_indices == [0]
    assert result.trajectories[0].insertion_depths_mm == [10.0]


def test_collision_beyond_target_does_not_block_short_insertion():
    result = analyze_case(
        case(
            [(0, 0, 5)],
            [config()],
            [ProtectedStructure(
                name="distal",
                geometry=SphereGeometry(center_mm=(0, 0, 15), radius_mm=1),
            )],
        )
    ).approaches[0]
    assert result.status == AnalysisStatus.COMPLETE


def test_collision_on_insertion_path_blocks_target():
    result = analyze_case(
        case(
            [(0, 0, 10)],
            [config()],
            [ProtectedStructure(
                name="proximal",
                geometry=SphereGeometry(center_mm=(0, 0, 5), radius_mm=1),
            )],
        )
    ).approaches[0]
    assert result.trajectories[0].reason == "protected_structure_collision"


def test_voxel_mask_collision_in_patient_coordinates(tmp_path):
    mask = np.zeros((9, 9, 9), dtype=np.uint8)
    mask[4, 4, 4] = 1
    path = tmp_path / "protected.npy"
    np.save(path, mask)
    affine = np.array([
        [1, 0, 0, -4],
        [0, 1, 0, -4],
        [0, 0, 2, 0],
        [0, 0, 0, 1],
    ], dtype=float)
    protected = ProtectedStructure(
        name="mask",
        geometry=VoxelGeometry(uri=path.name, affine=affine.tolist()),
    )
    result = analyze_case(
        case([(0, 0, 12)], [config(radius=0.1)], [protected]),
        base_directory=tmp_path,
    ).approaches[0]
    assert result.status == AnalysisStatus.NO_FEASIBLE_TRAJECTORY


def test_voxel_mask_out_of_fov_abstains_per_trajectory(tmp_path):
    mask = np.zeros((4, 4, 4), dtype=np.uint8)
    path = tmp_path / "small.npy"
    np.save(path, mask)
    protected = ProtectedStructure(
        name="small",
        geometry=VoxelGeometry(uri=path.name, affine=np.eye(4).tolist()),
    )
    result = analyze_case(
        case([(1, 1, 10)], [config(center=(1, 1, 1), radius=0.1)], [protected]),
        base_directory=tmp_path,
    ).approaches[0]
    assert result.trajectories[0].reason == "protected_mask_out_of_fov"
    assert result.status == AnalysisStatus.ABSTAINED


def test_missing_voxel_mask_abstains_whole_approach(tmp_path):
    protected = ProtectedStructure(
        name="missing",
        geometry=VoxelGeometry(uri="missing.npy", affine=np.eye(4).tolist()),
    )
    result = analyze_case(
        case([(0, 0, 5)], [config()], [protected]), base_directory=tmp_path
    ).approaches[0]
    assert result.status == AnalysisStatus.ABSTAINED


def test_mesh_backend_explicitly_abstains():
    protected = ProtectedStructure(
        name="mesh", geometry=MeshGeometry(uri="deferred.stl")
    )
    result = analyze_case(case([(0, 0, 5)], [config()], [protected])).approaches[0]
    assert result.status == AnalysisStatus.ABSTAINED
    assert "unsupported-mesh" in result.notes[0]


def test_portal_rejects_backward_direction():
    assert not portal_allows_direction(
        np.array([0.0, 0.0, -1.0]),
        np.array([0.0, 0.0, 1.0]),
        3.0,
        0.5,
    )


def test_solid_angle_and_directional_witness_metrics():
    cfg = config()
    result = analyze_case(case([(0, 0, 5)], [cfg])).approaches[0]
    # One pole cannot establish angular coverage of an entire cone.
    assert result.feasible_solid_angle_sr is None
    assert result.witness_polar_angle_deg == pytest.approx(0)
    assert result.witness_azimuth_deg == pytest.approx(0)


def test_same_portal_collinear_pair_is_not_collision_free():
    approaches = [config("scope"), config("instrument")]
    pair = analyze_case(
        case([(0, 0, 5)], approaches), simultaneous_minimum_angle_deg=0
    ).simultaneous_pairs[0]
    assert not pair.feasible


def test_pair_retains_each_instruments_reached_targets():
    approaches = [
        config("left", center=(-2, 0, 0), tolerance=0.2),
        config("right", center=(2, 0, 0), tolerance=0.2),
    ]
    pair = analyze_case(
        case([(-2, 0, 5), (2, 0, 5)], approaches),
        simultaneous_minimum_angle_deg=0,
    ).simultaneous_pairs[0]
    assert pair.feasible
    assert pair.first_reached_point_indices == [0]
    assert pair.second_reached_point_indices == [1]


def test_generated_analytic_clearances_match_independent_references():
    rng = np.random.default_rng(7301)
    for _ in range(8):
        start = rng.normal(size=3)
        end = start + rng.normal(size=3) * 3
        center = rng.normal(size=3) * 2
        analytic = capsule_sphere_clearance(start, end, 0.3, center, 0.7)
        brute = sampled_capsule_sphere_clearance(
            start, end, 0.3, center, 0.7, samples=20_001
        )
        assert analytic == pytest.approx(brute, abs=5e-4)


def test_segment_reference_converges_toward_analytic_result():
    first_start, first_end = np.array([0., 0, 0]), np.array([3., 2, 1])
    second_start, second_end = np.array([1., -2, 2]), np.array([2., 3, -1])
    exact = segment_segment_distance(first_start, first_end, second_start, second_end)
    coarse = sampled_segment_distance(
        first_start, first_end, second_start, second_end, samples=51
    )
    fine = sampled_segment_distance(
        first_start, first_end, second_start, second_end, samples=501
    )
    assert fine >= exact
    assert abs(fine - exact) <= abs(coarse - exact) + 1e-12
    assert fine == pytest.approx(exact, abs=0.01)
