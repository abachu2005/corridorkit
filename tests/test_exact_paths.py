import json

import numpy as np
import pytest

from skullbase_corridor.application.slicer_bridge import run_request
from skullbase_corridor.domain.models import (
    ApproachConfig,
    ApproachKind,
    CorridorCase,
    ExactPathCandidate,
    ExactPathState,
    KnowledgeStatus,
    PortalDisk,
    ProtectedStructure,
    RigidInstrument,
    SamplingConfig,
    SphereGeometry,
    TargetPointCloud,
    VirtualBoneRemoval,
    VoxelGeometry,
)
from skullbase_corridor.geometry.engine import analyze_case, evaluate_exact_path


def approach(*, allow_unknown=False, portal_radius=3.0, removals=None, center=(0, 0, 1)):
    return ApproachConfig(
        name="A",
        kind=ApproachKind.EEA,
        portal=PortalDisk(center_mm=center, normal=(0, 0, 1), radius_mm=portal_radius),
        nominal_direction=(0, 0, 1),
        instrument=RigidInstrument(length_mm=20, radius_mm=0.25),
        sampling=SamplingConfig(
            max_angle_deg=20,
            polar_steps=1,
            azimuth_steps=1,
            target_directed=False,
            adaptive_levels=0,
        ),
        target_tolerance_mm=0,
        allow_unknown_anatomy=allow_unknown,
        virtual_bone_removals=removals or [],
    )


def candidate(entry=(0, 0, 1), target=(0, 0, 10)):
    return ExactPathCandidate(
        candidate_id="candidate-1",
        approach_name="A",
        entry_point_mm=entry,
        target_point_mm=target,
    )


def corridor(config=None, protected=None):
    return CorridorCase(
        case_id="exact",
        target=TargetPointCloud(points_mm=[(0, 0, 10)]),
        protected_structures=protected or [],
        approaches=[config or approach()],
    )


def test_exact_collision_reports_finite_shaft_geometry():
    case = corridor(protected=[
        ProtectedStructure(
            name="critical",
            geometry=SphereGeometry(center_mm=(1, 0, 5), radius_mm=1),
        )
    ])
    result = evaluate_exact_path(case, candidate())
    assert result.state == ExactPathState.BLOCKED
    assert result.reason == "protected_structure_collision"
    assert result.minimum_clearance_mm == pytest.approx(-0.25)
    assert result.entry_point_mm == (0, 0, 1)
    assert result.tip_point_mm == (0, 0, 10)
    assert result.direction == pytest.approx((0, 0, 1))
    assert result.depth_mm == pytest.approx(9)


def test_exact_aperture_rejection_is_explicit():
    result = evaluate_exact_path(
        corridor(approach(portal_radius=0.2)),
        candidate(),
    )
    assert result.state == ExactPathState.BLOCKED
    assert result.reason == "portal_aperture"
    assert result.aperture_satisfied is False
    assert result.aperture_clearance_mm == pytest.approx(-0.05)


def test_unknown_anatomy_is_unavailable_or_conditional():
    protected = [
        ProtectedStructure(name="unknown", status=KnowledgeStatus.UNKNOWN)
    ]
    unavailable = evaluate_exact_path(corridor(protected=protected), candidate())
    assert unavailable.state == ExactPathState.UNAVAILABLE
    assert unavailable.reason == "protected_anatomy_unavailable"
    assert unavailable.unknown_anatomy == ["unknown:unknown"]

    conditional = evaluate_exact_path(
        corridor(approach(allow_unknown=True), protected), candidate()
    )
    assert conditional.state == ExactPathState.CONDITIONAL
    assert conditional.reason == "unknown_anatomy_allowed"


def test_out_of_fov_is_unavailable(tmp_path):
    path = tmp_path / "small.npy"
    np.save(path, np.zeros((4, 4, 4), dtype=np.uint8))
    protected = ProtectedStructure(
        name="small",
        geometry=VoxelGeometry(uri=path.name, affine=np.eye(4).tolist()),
    )
    result = evaluate_exact_path(
        corridor(protected=[protected]), candidate(entry=(1, 1, 1), target=(1, 1, 8)),
        base_directory=tmp_path,
    )
    assert result.state == ExactPathState.UNAVAILABLE
    assert result.reason == "protected_mask_out_of_fov"
    assert result.unknown_anatomy == ["small:out_of_fov"]


def test_reviewed_removal_uses_same_prepared_voxel_geometry(tmp_path):
    bone = np.zeros((15, 15, 15), dtype=np.uint8)
    bone[:, :, 5] = 1
    removal = bone.copy()
    np.save(tmp_path / "bone.npy", bone)
    np.save(tmp_path / "removal.npy", removal)
    affine = np.eye(4).tolist()
    geometry = VoxelGeometry(uri="bone.npy", affine=affine)
    structure = ProtectedStructure(
        name="bone",
        geometry=geometry,
        tissue_type="bone",
        allow_virtual_removal=True,
    )
    blocked = evaluate_exact_path(
        corridor(protected=[structure]),
        candidate(entry=(7, 7, 1), target=(7, 7, 10)),
        base_directory=tmp_path,
    )
    assert blocked.state == ExactPathState.BLOCKED

    reviewed = VirtualBoneRemoval(
        structure_name="bone",
        geometry=VoxelGeometry(uri="removal.npy", affine=affine),
        reviewed=True,
    )
    opened = evaluate_exact_path(
        corridor(approach(removals=[reviewed], center=(7, 7, 1)), [structure]),
        candidate(entry=(7, 7, 1), target=(7, 7, 10)),
        base_directory=tmp_path,
    )
    assert opened.state == ExactPathState.MODEL_FEASIBLE
    assert opened.required_removal_assumptions == ["bone"]


def test_exact_path_matches_sampled_witness():
    protected = [
        ProtectedStructure(
            name="offset",
            geometry=SphereGeometry(center_mm=(2, 0, 5), radius_mm=0.5),
        )
    ]
    case = corridor(protected=protected)
    sampled = analyze_case(case).approaches[0].trajectories[0]
    exact = evaluate_exact_path(case, candidate())
    assert exact.state == ExactPathState.MODEL_FEASIBLE
    assert exact.entry_point_mm == sampled.entry_point_mm
    assert exact.direction == pytest.approx(sampled.direction)
    assert exact.depth_mm == pytest.approx(sampled.insertion_depths_mm[0])
    assert exact.minimum_clearance_mm == pytest.approx(sampled.minimum_clearance_mm)


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        (
            ExactPathCandidate(
                candidate_id="bad-approach",
                approach_name="missing",
                entry_point_mm=(0, 0, 1),
                target_point_mm=(0, 0, 2),
            ),
            "approach_not_found",
        ),
        (
            ExactPathCandidate(
                candidate_id="zero",
                approach_name="A",
                entry_point_mm=(0, 0, 1),
                target_point_mm=(0, 0, 1),
            ),
            "entry_and_target_coincident",
        ),
    ],
)
def test_invalid_candidates_return_typed_results(path, reason):
    result = evaluate_exact_path(corridor(), path)
    assert result.state == ExactPathState.INVALID
    assert result.reason == reason


def test_bridge_optionally_exports_exact_candidates(tmp_path):
    target = np.zeros((12, 12, 12), dtype=np.uint8)
    target[5, 5, 9] = 1
    protected = np.zeros_like(target)
    protected[1, 1, 1] = 1
    np.save(tmp_path / "target.npy", target)
    np.save(tmp_path / "protected.npy", protected)
    request = {
        "target": "target.npy",
        "protected": [{"name": "critical", "path": "protected.npy"}],
        "affine": np.eye(4).tolist(),
        "entries": {"EEA": [5, 5, 2], "CTM": []},
        "target_point": [5, 5, 9],
        "shaft_diameter_mm": 0.5,
        "portal_diameter_mm": 4,
        "instrument_length_mm": 20,
        "anatomy_complete": True,
        "reviewer": "test",
        "sampling": {"polar_steps": 1, "azimuth_steps": 1},
        "exact_candidates": [{
            "candidate_id": "selected",
            "approach_name": "EEA",
            "entry_point_mm": [5, 5, 2],
            "target_point_mm": [5, 5, 9],
        }],
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request))
    envelope = run_request(request_path, tmp_path / "output")
    assert envelope["exact_paths"][0]["candidate_id"] == "selected"
    assert envelope["exact_paths"][0]["state"] == "model_feasible"
