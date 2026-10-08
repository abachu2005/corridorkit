"""Analytical closed-cell fixtures for opt-in bounded collision refinement."""

import numpy as np
import pytest

from skullbase_corridor.domain.models import VoxelGeometry
from skullbase_corridor.geometry.voxel import VoxelMaskBackend


def backend(tmp_path, *, refine=True, budget=128, affine=None, indices=((3, 3, 3),)):
    mask = np.zeros((9, 9, 9), dtype=np.uint8)
    for index in indices:
        mask[index] = 1
    path = tmp_path / "cells.npy"
    np.save(path, mask)
    return VoxelMaskBackend(VoxelGeometry(
        uri=str(path), affine=(np.eye(4) if affine is None else affine).tolist(),
        refine_near_boundary=refine, max_refinement_cells=budget,
    ))


def test_default_is_frozen_coarse_backend_and_refinement_recovers_clearance(tmp_path):
    start, end = np.array([3.75, 3, 1.]), np.array([3.75, 3, 5.])
    coarse = backend(tmp_path, refine=False)
    refined = backend(tmp_path)
    assert not VoxelGeometry(uri="x", affine=np.eye(4).tolist()).refine_near_boundary
    old = coarse.capsule_clearance(start, end, .2)
    result = refined.capsule_clearance(start, end, .2)
    # Nearest cell face is x=3.5: centerline distance .25 minus radius .2.
    assert old.clearance_mm < 0 and not old.refined
    assert result.refined and 0 < result.clearance_mm <= .05
    assert result.clearance_mm == pytest.approx(.05, abs=2e-9)
    assert refined.refinement_attempts == refined.refinement_completed == 1
    assert coarse.refinement_attempts == 0


@pytest.mark.parametrize("x,radius", [(3.7, .2), (3., 0.), (3.5, 0.)])
def test_tangent_and_inside_closed_cell_remain_blocked(tmp_path, x, radius):
    b = backend(tmp_path)
    result = b.capsule_clearance(np.array([x, 3, 1.]), np.array([x, 3, 5.]), radius)
    assert result.refined
    assert result.clearance_mm <= 0


def test_sheared_cell_clearance_and_tangency(tmp_path):
    affine = np.eye(4)
    affine[0, 1] = .5
    b = backend(tmp_path, affine=affine)
    center = (affine @ np.array([3., 3, 3, 1]))[:3]
    # Face x-.5*y = constant has outward unit normal proportional to (1,-.5,0).
    normal = np.array([1., -.5, 0]) / np.sqrt(1.25)
    face_center = center + np.array([.5, 0, 0])
    for gap in [0., .04]:
        point = face_center + normal * (.2 + gap)
        result = b.capsule_clearance(point, point, .2)
        assert result.refined
        assert result.clearance_mm == pytest.approx(gap, abs=2e-9)
        if gap == 0:
            assert result.clearance_mm <= 0
        else:
            assert 0 < result.clearance_mm <= gap


def test_budget_falls_back_to_identical_coarse_bound_without_allocating_cells(tmp_path):
    indices = ((3, 3, 3), (3, 3, 4))
    coarse = backend(tmp_path, refine=False, indices=indices)
    limited = backend(tmp_path, budget=1, indices=indices)
    start, end = np.array([3.75, 3, 1.]), np.array([3.75, 3, 6.])
    old = coarse.capsule_clearance(start, end, .2)
    result = limited.capsule_clearance(start, end, .2)
    assert result.clearance_mm == old.clearance_mm < 0
    assert result.refinement_budget_exceeded and not result.refined
    assert limited.refinement_budget_exceeded == limited.refinement_attempts == 1
    assert limited.refinement_completed == 0


def test_refinement_never_overrides_fov_unknown(tmp_path):
    b = backend(tmp_path)
    result = b.capsule_clearance(np.array([3., 3, 0]), np.array([3., 3, 12]), .2)
    assert result.out_of_fov and result.clearance_mm is None
    assert not result.refined and not result.refinement_budget_exceeded
    assert b.refinement_attempts == 0


def test_clearance_is_capped_and_omitted_cells_cannot_invalidate_it(tmp_path):
    b = backend(tmp_path, indices=((3, 3, 3), (8, 8, 8)))
    # .35 face gap minus .2 radius = .15, but report at most .1.
    start, end = np.array([3.85, 3, 1.]), np.array([3.85, 3, 5.])
    result = b.capsule_clearance(start, end, .2)
    assert result.refined and result.clearance_mm == .1


def test_positive_coarse_query_does_not_run_refinement(tmp_path):
    b = backend(tmp_path)
    result = b.capsule_clearance(np.array([6., 3, 1]), np.array([6., 3, 5]), .2)
    assert result.clearance_mm > 0 and not result.refined
    assert b.refinement_attempts == 0


def test_empty_broad_phase_proves_only_capped_clearance(tmp_path):
    b = backend(tmp_path, indices=((3, 3, 4),))
    # Deliberately coarse sampling creates a negative bound despite a remote
    # obstacle; an empty exact broad phase still proves clearance >= ceiling.
    b.sample_step_mm = 100
    start, end = np.array([3., 3, 0]), np.array([3., 3, 2])
    result = b.capsule_clearance(start, end, .2)
    # Ball radius is 1 + sqrt(3)/2 + .2 + .1; the cell center is 3 mm away.
    assert result.refined and result.clearance_mm == .1


def test_fov_abstains_before_allocating_centerline_samples(tmp_path, monkeypatch):
    b = backend(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("out-of-FOV queries must not allocate samples")

    monkeypatch.setattr(np, "linspace", forbidden)
    result = b.capsule_clearance([3, 3, 3], [3, 3, 1e100], .2)
    assert result.out_of_fov and result.clearance_mm is None
    assert b.refinement_attempts == 0


@pytest.mark.parametrize("axis", range(3))
@pytest.mark.parametrize("side", [-1, 1])
def test_sheared_anisotropic_fov_uses_physical_capsule_margin(tmp_path, axis, side):
    affine = np.eye(4)
    affine[:3, :3] = [[.3, .7, -.1], [0, 2., .4], [0, 0, 1.2]]
    affine[:3, 3] = [123., -456., 789.]
    b = backend(tmp_path, affine=affine)
    radius = .12
    margin = radius * np.linalg.norm(np.linalg.inv(affine[:3, :3]), axis=1)
    for excess in (-1e-6, 1e-6):
        index = np.full(3, 4.)
        index[axis] = (-.5 + margin[axis] - excess if side < 0
                       else 8.5 - margin[axis] + excess)
        point = affine[:3, :3] @ index + affine[:3, 3]
        result = b.capsule_clearance(point, point, radius)
        assert result.out_of_fov == (excess > 0)
        if excess > 0:
            assert result.clearance_mm is None and not result.refined


def test_budget_is_checked_before_reference_construction(tmp_path, monkeypatch):
    from skullbase_corridor.geometry import mesh_reference

    b = backend(tmp_path, budget=1, indices=((3, 3, 3), (3, 3, 4)))

    def forbidden(*args, **kwargs):
        raise AssertionError("exhausted budget must not allocate triangle cells")

    monkeypatch.setattr(mesh_reference, "VoxelCellReference", forbidden)
    result = b.capsule_clearance([3.75, 3, 1], [3.75, 3, 6], .2)
    assert result.refinement_budget_exceeded and result.clearance_mm < 0


def test_exact_cell_budget_completes(tmp_path):
    b = backend(tmp_path, budget=2, indices=((3, 3, 3), (3, 3, 4)))
    result = b.capsule_clearance([3.75, 3, 1], [3.75, 3, 6], .2)
    assert result.refined and not result.refinement_budget_exceeded
    assert result.clearance_mm == pytest.approx(.05, abs=2e-9)
