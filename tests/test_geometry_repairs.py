"""Independent physical fixtures for uncertainty, sampling, and mask contracts."""

import math

import nibabel as nib
import numpy as np
import pytest
from pydantic import ValidationError

from skullbase_corridor.domain.models import (
    AnalysisStatus, ApproachConfig, CorridorCase, KnowledgeStatus, PortalDisk,
    ProtectedStructure, RigidInstrument, SampledMaskTarget, SamplingConfig,
    SphereGeometry, TargetPointCloud, VirtualBoneRemoval, VoxelGeometry,
)
from skullbase_corridor.geometry.engine import (
    AnalysisCancelled, analyze_approach, analyze_case, analyze_simultaneous_pair,
)
from skullbase_corridor.geometry.voxel import VoxelMaskBackend


def approach(name="A", **updates):
    values = dict(
        name=name, kind="eea",
        portal=PortalDisk(center_mm=(0, 0, 0), normal=(0, 0, 1), radius_mm=4),
        nominal_direction=(0, 0, 1),
        instrument=RigidInstrument(length_mm=20, radius_mm=.2),
        target_tolerance_mm=0,
        sampling=SamplingConfig(
            max_angle_deg=20, polar_steps=1, azimuth_steps=1, adaptive_levels=0,
        ),
    )
    values.update(updates)
    return ApproachConfig(**values)


def case(points=((0, 0, 10),), approaches=None, structures=()):
    return CorridorCase(
        case_id="independent", target=TargetPointCloud(points_mm=points, point_volume_mm3=2),
        approaches=approaches or [approach()], protected_structures=list(structures),
    )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_coordinates_and_dimensions_rejected(bad):
    constructors = [
        lambda: TargetPointCloud(points_mm=[(bad, 0, 0)]),
        lambda: SphereGeometry(center_mm=(bad, 0, 0), radius_mm=1),
        lambda: SphereGeometry(center_mm=(0, 0, 0), radius_mm=bad),
        lambda: PortalDisk(center_mm=(0, bad, 0), normal=(0, 0, 1), radius_mm=1),
        lambda: PortalDisk(center_mm=(0, 0, 0), normal=(0, bad, 1), radius_mm=1),
        lambda: approach(nominal_direction=(0, bad, 1)),
        lambda: RigidInstrument(length_mm=bad, radius_mm=1),
    ]
    for construct in constructors:
        with pytest.raises(ValidationError):
            construct()


@pytest.mark.parametrize("row", [[0, 0, 0, 2], [0, 0, .1, 1], [0, 0, 0, float("nan")]])
def test_nonhomogeneous_affines_rejected(row):
    affine = np.eye(4)
    affine[3] = row
    with pytest.raises(ValidationError):
        VoxelGeometry(uri="x.npy", affine=affine.tolist())
    with pytest.raises(ValidationError):
        SampledMaskTarget(
            points_mm=[(0, 0, 0)], source_shape=(1, 1, 1),
            voxel_volume_mm3=1, affine=affine.tolist(),
        )


def test_duplicate_names_and_ambiguous_or_mixed_frames_rejected():
    with pytest.raises(ValidationError, match="unique"):
        case(approaches=[approach(), approach()])
    values = case().model_dump()
    assert values["coordinate_frame"] == "RAS"
    values["coordinate_frame"] = "physical_mm"
    with pytest.raises(ValidationError):
        CorridorCase.model_validate(values)
    values["coordinate_frame"] = "LPS"
    with pytest.raises(ValidationError, match="converted"):
        CorridorCase.model_validate(values)
    values["target"]["coordinate_frame"] = "LPS"
    assert CorridorCase.model_validate(values).coordinate_frame == "LPS"


@pytest.mark.parametrize("allow", [False, True])
def test_unknown_never_produces_complete_metrics_or_pair(allow):
    configs = [approach("A", allow_unknown_anatomy=allow),
               approach("B", allow_unknown_anatomy=allow)]
    c = case(approaches=configs, structures=[
        ProtectedStructure(name="unreviewed", status=KnowledgeStatus.UNKNOWN),
    ])
    result = analyze_case(c, simultaneous_minimum_angle_deg=0)
    for item in result.approaches:
        assert item.status == (AnalysisStatus.INCOMPLETE if allow else AnalysisStatus.ABSTAINED)
        assert item.reached_measure_mm3 is None
        assert item.feasible_solid_angle_sr is None
        assert item.best_working_depth_mm is None
        assert item.reached_point_indices == []
        assert all(t.conditional and t.minimum_clearance_mm is None for t in item.trajectories)
    assert result.combinations[0].status == AnalysisStatus.INCOMPLETE
    assert result.combinations[0].union_indices == []
    assert result.simultaneous_pairs[0].feasible is None
    assert result.simultaneous_pairs[0].shaft_clearance_mm is None


def test_missing_mask_does_not_drop_other_loaded_masks(tmp_path):
    affine = np.eye(4)
    affine[:3, 3] = [-4, -4, -1]
    mask = np.zeros((9, 9, 20), dtype=np.uint8)
    mask[4, 4, 6] = 1
    np.save(tmp_path / "block.npy", mask)
    structures = [
        ProtectedStructure(name="missing", geometry=VoxelGeometry(
            uri="absent.npy", affine=affine.tolist())),
        ProtectedStructure(name="retained", geometry=VoxelGeometry(
            uri="block.npy", affine=affine.tolist())),
    ]
    result = analyze_case(
        case(approaches=[approach(allow_unknown_anatomy=True)], structures=structures),
        base_directory=tmp_path,
    ).approaches[0]
    assert result.status == AnalysisStatus.INCOMPLETE
    assert result.trajectories[0].reason == "protected_structure_collision"
    assert not result.trajectories[0].feasible


def test_target_directed_witness_finds_exact_off_grid_target():
    # Independent vector: atan(1/10) lies in cap, but not at its sampled pole.
    c = case(points=[(1, 0, 10)])
    result = analyze_case(c).approaches[0]
    assert result.trajectories[0].reason == "target_not_reached"
    witness = next(t for t in result.trajectories if t.feasible)
    np.testing.assert_allclose(witness.direction, np.array([1, 0, 10]) / math.sqrt(101))
    assert witness.insertion_depths_mm == pytest.approx([math.sqrt(101)])
    assert result.best_working_depth_mm == pytest.approx(math.sqrt(101))
    assert result.best_insertion_span_mm == 0
    assert result.feasible_solid_angle_sr is None
    assert not result.sampling_complete


def test_adaptive_refinement_finds_bypass_of_tangent_center_ray():
    # Center ray touches the sphere; tilting away remains within target tolerance.
    sphere = ProtectedStructure(name="sphere", geometry=SphereGeometry(
        center_mm=(1.2, 0, 10), radius_mm=1,
    ))
    sampling = SamplingConfig(max_angle_deg=4, polar_steps=1, azimuth_steps=1,
                              target_directed=True, adaptive_levels=1)
    result = analyze_case(case(
        approaches=[approach(sampling=sampling, target_tolerance_mm=.5)],
        structures=[sphere],
    )).approaches[0]
    assert result.trajectories[0].reason == "protected_structure_collision"
    refined = [t for t in result.trajectories if t.feasible and t.sampling_source == "adaptive"]
    assert refined
    # Independent direct distance to segment using scalar projection.
    for trajectory in refined:
        direction = np.array(trajectory.direction)
        endpoint = direction * trajectory.insertion_depths_mm[0]
        center = np.array([1.2, 0, 10])
        parameter = np.clip(center.dot(endpoint) / endpoint.dot(endpoint), 0, 1)
        assert np.linalg.norm(center - parameter * endpoint) > 1.2


def test_no_sampled_path_is_not_certified_infeasibility():
    sampling = SamplingConfig(polar_steps=1, azimuth_steps=1, target_directed=False,
                              adaptive_levels=0)
    result = analyze_case(case(points=[(1, 0, 10)],
                               approaches=[approach(sampling=sampling)])).approaches[0]
    assert result.status == AnalysisStatus.NO_FEASIBLE_TRAJECTORY
    assert not result.infeasibility_certified
    assert not result.sampling_complete
    assert result.feasible_solid_angle_sr is None
    assert any("not certified infeasibility" in note for note in result.notes)


def test_radial_tip_reach_cannot_bypass_obstacle_at_offset_target():
    # Shaft clears this sphere, but deployed tip envelope and reached point do not.
    result = analyze_case(case(
        points=[(1.5, 0, 10)],
        approaches=[approach(
            instrument=RigidInstrument(length_mm=20, radius_mm=.1, tip_working_radius_mm=1.5),
            sampling=SamplingConfig(polar_steps=1, target_directed=False, adaptive_levels=0),
        )],
        structures=[ProtectedStructure(name="tip-obstacle", geometry=SphereGeometry(
            center_mm=(1.5, 0, 10), radius_mm=.2))],
    )).approaches[0]
    assert result.status == AnalysisStatus.NO_FEASIBLE_TRAJECTORY
    assert result.trajectories[0].reason == "protected_structure_collision"


def test_finite_entry_offsets_can_avoid_center_obstacle():
    sampling = SamplingConfig(polar_steps=1, azimuth_steps=1, adaptive_levels=0,
                              portal_offset_rings=1, portal_offset_azimuth_steps=4)
    result = analyze_case(case(
        approaches=[approach(sampling=sampling)],
        structures=[ProtectedStructure(name="center", geometry=SphereGeometry(
            center_mm=(0, 0, 1), radius_mm=.5))],
    )).approaches[0]
    assert result.trajectories[0].reason == "protected_structure_collision"
    assert result.status == AnalysisStatus.COMPLETE
    entry = np.array(result.witness_entry_point_mm)
    assert np.linalg.norm(entry) > 0
    assert entry[2] == pytest.approx(0)
    assert np.linalg.norm(entry) < 4


def test_legacy_shared_overlap_is_explicitly_unsupported():
    result = analyze_case(case(approaches=[
        approach("A", shared_portal_overlap_mm=10),
        approach("B", shared_portal_overlap_mm=10),
    ]), simultaneous_minimum_angle_deg=0)
    pair = result.simultaneous_pairs[0]
    assert pair.status == AnalysisStatus.UNSUPPORTED
    assert pair.feasible is None
    assert pair.shaft_clearance_mm is None


def test_cancellation_at_entry_and_inside_target_and_pair_loops():
    c = case(points=[(0, 0, depth) for depth in range(1, 15)],
             approaches=[approach("A"), approach("B")])
    for analyze in [lambda: analyze_case(c, cancel_check=lambda: True),
                    lambda: analyze_approach(c, c.approaches[0], cancel_check=lambda: True)]:
        with pytest.raises(AnalysisCancelled):
            analyze()
    calls = 0

    def cancel_later():
        nonlocal calls
        calls += 1
        return calls >= 7

    with pytest.raises(AnalysisCancelled):
        analyze_approach(c, c.approaches[0], cancel_check=cancel_later)
    assert calls == 7
    results = analyze_case(c).approaches
    calls = 0

    def pair_cancel():
        nonlocal calls
        calls += 1
        return calls >= 2

    with pytest.raises(AnalysisCancelled):
        analyze_simultaneous_pair(
            c.approaches[0], results[0], c.approaches[1], results[1],
            minimum_angle_deg=0, cancel_check=pair_cancel,
        )


def test_nifti_affine_mismatch_abstains_and_matching_lps_is_accepted(tmp_path):
    path = tmp_path / "mask.nii.gz"
    affine = np.eye(4)
    affine[:3, 3] = [-5, -5, -1]
    nib.save(nib.Nifti1Image(np.zeros((11, 11, 20), dtype=np.uint8), affine), path)
    wrong = VoxelGeometry(uri=str(path), affine=np.eye(4).tolist())
    result = analyze_case(case(structures=[
        ProtectedStructure(name="misregistered", geometry=wrong),
    ])).approaches[0]
    assert result.status == AnalysisStatus.ABSTAINED
    assert "affine" in result.notes[0]
    lps = np.diag([-1., -1., 1., 1.]) @ affine
    backend = VoxelMaskBackend(VoxelGeometry(
        uri=str(path), affine=lps.tolist(), coordinate_frame="LPS",
    ))
    query = backend.capsule_clearance(np.array([0, 0, 0]), np.array([0, 0, 10]), .2)
    assert not query.out_of_fov


@pytest.mark.parametrize("frame", ["RAS", "LPS"])
def test_nrrd_preserves_anatomical_space_and_affine(tmp_path, frame):
    # Independent raw NRRD fixture: one voxel at index (2,2,3).
    path = tmp_path / f"mask-{frame}.nrrd"
    data = np.zeros((5, 5, 8), dtype=np.uint8)
    data[2, 2, 3] = 1
    space = "right-anterior-superior" if frame == "RAS" else "left-posterior-superior"
    header = (
        f"NRRD0005\ntype: uint8\ndimension: 3\nspace: {space}\n"
        "sizes: 5 5 8\nspace directions: (1,0,0) (0,1,0) (0,0,1)\n"
        "kinds: domain domain domain\nencoding: raw\nspace origin: (-2,-2,-1)\n\n"
    )
    path.write_bytes(header.encode("ascii") + data.tobytes(order="F"))
    affine = np.eye(4)
    affine[:3, 3] = [-2, -2, -1]
    backend = VoxelMaskBackend(VoxelGeometry(
        uri=str(path), affine=affine.tolist(), coordinate_frame=frame,
    ))
    result = backend.capsule_clearance(np.array([0., 0, 0]), np.array([0., 0, 5]), .1)
    assert not result.out_of_fov
    assert result.clearance_mm < 0


def test_voxel_oblique_anisotropic_cell_has_no_false_clearance(tmp_path):
    # A point inside a sheared foreground voxel must never be labelled clear.
    affine = np.array([[2, .5, 0, -4], [0, 1, .2, -2], [0, 0, 3, -6], [0, 0, 0, 1.]])
    mask = np.zeros((5, 5, 5), dtype=np.uint8)
    mask[2, 2, 2] = 1
    np.save(tmp_path / "sheared.npy", mask)
    backend = VoxelMaskBackend(VoxelGeometry(
        uri=str(tmp_path / "sheared.npy"), affine=affine.tolist(),
    ))
    for local in [(0, 0, 0), (.49, -.49, .49), (-.49, .49, -.49)]:
        point = (affine @ np.r_[np.array([2, 2, 2]) + local, 1])[:3]
        result = backend.capsule_clearance(point, point, 0)
        assert not result.out_of_fov
        assert result.clearance_mm <= 0


def test_voxel_tangent_face_never_reports_positive_clearance(tmp_path):
    mask = np.zeros((7, 7, 7), dtype=np.uint8)
    mask[3, 3, 3] = 1
    np.save(tmp_path / "voxel.npy", mask)
    backend = VoxelMaskBackend(VoxelGeometry(
        uri=str(tmp_path / "voxel.npy"), affine=np.eye(4).tolist(),
    ))
    # The cell starts at x=2.5. A radius .5 capsule on x=2 touches that face.
    result = backend.capsule_clearance(np.array([2., 3, 1]), np.array([2., 3, 5]), .5)
    assert not result.out_of_fov
    assert result.clearance_mm <= 0


def test_partial_fov_suppresses_even_known_reached_volume(tmp_path):
    affine = np.eye(4)
    affine[:3, 3] = [-3, -3, -1]
    np.save(tmp_path / "empty.npy", np.zeros((7, 7, 9), dtype=np.uint8))
    protected = ProtectedStructure(name="cropped", geometry=VoxelGeometry(
        uri=str(tmp_path / "empty.npy"), affine=affine.tolist(),
    ))
    result = analyze_case(case(points=[(0, 0, 5), (0, 0, 10)], structures=[protected])).approaches[0]
    assert result.status == AnalysisStatus.ABSTAINED
    assert result.reached_measure_mm3 is None
    assert result.feasible_solid_angle_sr is None
    assert result.reached_point_indices == []
    assert result.trajectories[0].conditional
    assert result.trajectories[0].reached_point_indices == [0]


def test_invalid_mask_values_and_undeclared_nrrd_space_are_rejected(tmp_path):
    invalid = np.zeros((2, 2, 2))
    invalid[0, 0, 0] = np.nan
    np.save(tmp_path / "invalid.npy", invalid)
    with pytest.raises(ValueError, match="finite"):
        VoxelMaskBackend(VoxelGeometry(
            uri=str(tmp_path / "invalid.npy"), affine=np.eye(4).tolist(),
        ))
    path = tmp_path / "ambiguous.nrrd"
    path.write_bytes(
        b"NRRD0005\ntype: uint8\ndimension: 3\nsizes: 2 2 2\nencoding: raw\n\n"
        + bytes(8)
    )
    with pytest.raises(ValueError, match="explicitly declare"):
        VoxelMaskBackend(VoxelGeometry(uri=str(path), affine=np.eye(4).tolist()))


def bone_fixture(tmp_path, *, critical=False, removal_shape=None):
    affine = np.eye(4)
    affine[:3, 3] = [-4, -4, -1]
    mask = np.zeros((9, 9, 20), dtype=np.uint8)
    mask[4, 4, 6] = 1
    np.save(tmp_path / "bone.npy", mask)
    removed = mask if removal_shape is None else np.zeros(removal_shape, dtype=np.uint8)
    np.save(tmp_path / "remove.npy", removed)
    bone_geometry = VoxelGeometry(uri="bone.npy", affine=affine.tolist())
    removal_geometry = VoxelGeometry(uri="remove.npy", affine=affine.tolist())
    structures = [ProtectedStructure(
        name="bone", geometry=bone_geometry, tissue_type="bone", allow_virtual_removal=True,
    )]
    if critical:
        structures.append(ProtectedStructure(name="critical", geometry=bone_geometry))
    removal = VirtualBoneRemoval(structure_name="bone", geometry=removal_geometry, reviewed=True)
    return structures, removal


def test_virtual_bone_removal_is_approach_specific_and_never_removes_critical(tmp_path):
    structures, removal = bone_fixture(tmp_path)
    configs = [approach("intact"), approach("removed", virtual_bone_removals=[removal])]
    result = analyze_case(case(approaches=configs, structures=structures), base_directory=tmp_path)
    assert result.approaches[0].status == AnalysisStatus.NO_FEASIBLE_TRAJECTORY
    assert result.approaches[1].status == AnalysisStatus.COMPLETE
    structures, removal = bone_fixture(tmp_path, critical=True)
    result = analyze_case(case(
        approaches=[approach(virtual_bone_removals=[removal])], structures=structures,
    ), base_directory=tmp_path)
    assert result.approaches[0].status == AnalysisStatus.NO_FEASIBLE_TRAJECTORY


def test_virtual_removal_requires_review_permission_matching_grid_and_subset(tmp_path):
    structures, removal = bone_fixture(tmp_path)
    for changed in [removal.model_copy(update={"reviewed": False}),
                    removal.model_copy(update={"structure_name": "critical"})]:
        with pytest.raises(ValidationError):
            case(approaches=[approach(virtual_bone_removals=[changed])], structures=structures)
    forbidden = structures[0].model_copy(update={"allow_virtual_removal": False})
    with pytest.raises(ValidationError):
        case(approaches=[approach(virtual_bone_removals=[removal])], structures=[forbidden])
    structures, removal = bone_fixture(tmp_path, removal_shape=(3, 3, 3))
    result = analyze_case(case(
        approaches=[approach(virtual_bone_removals=[removal])], structures=structures,
    ), base_directory=tmp_path).approaches[0]
    assert result.status == AnalysisStatus.ABSTAINED
    assert "shape" in result.notes[0]
    structures, removal = bone_fixture(tmp_path)
    np.save(tmp_path / "remove.npy", np.ones((9, 9, 20), dtype=np.uint8))
    result = analyze_case(case(
        approaches=[approach(virtual_bone_removals=[removal])], structures=structures,
    ), base_directory=tmp_path).approaches[0]
    assert result.status == AnalysisStatus.ABSTAINED
    assert "subset" in result.notes[0]
    changed_affine = np.asarray(removal.geometry.affine).copy()
    changed_affine[0, 3] += .5
    mismatched = removal.model_copy(update={
        "geometry": removal.geometry.model_copy(update={"affine": changed_affine.tolist()}),
    })
    with pytest.raises(ValidationError, match="affine"):
        case(approaches=[approach(virtual_bone_removals=[mismatched])], structures=structures)
