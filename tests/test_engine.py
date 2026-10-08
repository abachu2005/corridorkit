import numpy as np

from corridorkit.domain.models import (
    AnalysisStatus,
    ApproachConfig,
    ApproachKind,
    CorridorCase,
    KnowledgeStatus,
    PortalDisk,
    ProtectedStructure,
    RigidInstrument,
    SamplingConfig,
    SphereGeometry,
    TargetPointCloud,
)
from corridorkit.geometry.engine import analyze_case


def approach(name: str, center=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0), length=20.0):
    return ApproachConfig(
        name=name,
        kind=ApproachKind.EEA,
        portal=PortalDisk(center_mm=center, normal=direction, radius_mm=4.0),
        nominal_direction=direction,
        instrument=RigidInstrument(length_mm=length, radius_mm=0.5),
        sampling=SamplingConfig(
            max_angle_deg=1, polar_steps=1, azimuth_steps=1,
            target_directed=False, adaptive_levels=0,
        ),
        target_tolerance_mm=0.5,
    )


def test_finite_length_reach_and_working_depth():
    case = CorridorCase(
        case_id="length",
        target=TargetPointCloud(points_mm=[(0, 0, 5), (0, 0, 15), (0, 0, 21)]),
        approaches=[approach("A")],
    )
    result = analyze_case(case).approaches[0]
    assert result.reached_point_indices == [0, 1]
    assert result.best_working_depth_mm == 15
    assert result.best_insertion_span_mm == 10


def test_union_intersection_and_incremental_have_no_double_count():
    case = CorridorCase(
        case_id="sets",
        target=TargetPointCloud(points_mm=[(0, 0, 5), (3, 0, 5), (6, 0, 5)]),
        approaches=[
            approach("A"),
            approach("B", center=(3, 0, 0)),
        ],
    )
    result = analyze_case(case)
    combination = result.combinations[0]
    a = set(result.approaches[0].reached_point_indices)
    b = set(result.approaches[1].reached_point_indices)
    assert set(combination.union_indices) == a | b
    assert set(combination.intersection_indices) == a & b
    assert set(combination.incremental_first_indices).isdisjoint(b)
    assert len(combination.union_indices) == len(a) + len(b) - len(a & b)


def test_unknown_anatomy_causes_abstention():
    case = CorridorCase(
        case_id="unknown",
        target=TargetPointCloud(points_mm=[(0, 0, 5)]),
        protected_structures=[
            ProtectedStructure(name="carotid", status=KnowledgeStatus.OUT_OF_FOV)
        ],
        approaches=[approach("A")],
    )
    assert analyze_case(case).approaches[0].status == AnalysisStatus.ABSTAINED


def test_collision_tangency_is_infeasible():
    case = CorridorCase(
        case_id="tangent",
        target=TargetPointCloud(points_mm=[(0, 0, 10)]),
        protected_structures=[
            ProtectedStructure(
                name="sphere",
                geometry=SphereGeometry(center_mm=(2.5, 0, 10), radius_mm=2.0),
            )
        ],
        approaches=[approach("A")],
    )
    result = analyze_case(case).approaches[0]
    assert result.status == AnalysisStatus.NO_FEASIBLE_TRAJECTORY
    assert result.trajectories[0].reason == "protected_structure_collision"


def test_simultaneous_pair_enforces_shaft_collision():
    case = CorridorCase(
        case_id="pair",
        target=TargetPointCloud(points_mm=[(0, 0, 5), (0, 5, 0)]),
        approaches=[
            approach("A", center=(-5, 0, 0), direction=(1, 0, 0), length=10),
            approach("B", center=(0, -5, 0), direction=(0, 1, 0), length=10),
        ],
    )
    pair = analyze_case(case, simultaneous_minimum_angle_deg=80).simultaneous_pairs[0]
    assert not pair.feasible
