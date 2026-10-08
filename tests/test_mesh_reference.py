import math

import numpy as np
import pytest

from corridorkit.domain.models import VoxelGeometry
from corridorkit.geometry.mesh_reference import (
    VoxelCellReference,
    segment_mesh_distance,
    segment_triangle_distance,
    triangulate_voxel_cells,
)
from corridorkit.geometry.voxel import VoxelMaskBackend


@pytest.mark.parametrize(("start", "end", "expected"), [
    ((.2, .2, -1), (.2, .2, 1), 0.),
    ((.2, .2, 2), (.3, .3, 2), 2.),
    ((-1, 0, 0), (2, 0, 0), 0.),
    ((2, 0, 0), (2, 0, 0), 1.),
    ((.2, .2, 0), (.2, .2, 0), 0.),
])
def test_triangle_features(start, end, expected):
    assert segment_triangle_distance(start, end, [(0, 0, 0), (1, 0, 0), (0, 1, 0)]) == pytest.approx(expected)


def test_degenerate_triangle_and_finite_inputs():
    assert segment_triangle_distance((0, 1, 0), (2, 1, 0),
                                     [(0, 0, 0), (1, 0, 0), (2, 0, 0)]) == pytest.approx(1)
    for invalid in (np.nan, np.inf, -np.inf):
        with pytest.raises(ValueError):
            segment_triangle_distance((invalid, 0, 0), (0, 0, 0), np.zeros((3, 3)))
        with pytest.raises(ValueError):
            triangulate_voxel_cells([[0, 0, 0]], np.full((4, 4), invalid))
    with pytest.raises(ValueError):
        segment_mesh_distance((0, 0, 0), (0, 0, 0), [])
    with pytest.raises(ValueError):
        triangulate_voxel_cells([[.1, 0, 0]], np.eye(4))
    with pytest.raises(ValueError):
        triangulate_voxel_cells([[0, 0, 0]], np.zeros((4, 4)))


def test_solid_interior_crossing_and_near_boundary():
    reference = VoxelCellReference([[0, 0, 0]], np.eye(4))
    assert reference.triangles.shape == (12, 3, 3)
    assert reference.segment_distance((0, 0, 0), (.1, .1, .1)) == 0
    assert reference.segment_distance((-2, 0, 0), (2, 0, 0)) == 0
    # Surface distance differs from solid distance for an entirely interior segment.
    assert segment_mesh_distance((0, 0, 0), (0, 0, 0), reference.triangles) == pytest.approx(.5)
    for delta in (-1e-6, 0., 1e-6):
        query = reference.capsule_query((.75 + delta, -.2, 0), (.75 + delta, .2, 0), .25)
        assert query.centerline_distance_mm == pytest.approx(.25 + delta)
        assert query.collides == (delta <= 0)
    assert reference.segment_distance((1.5, 1.5, .5), (1.5, 1.5, .5)) == pytest.approx(math.sqrt(2))
    for radius in (-1, np.nan, np.inf):
        with pytest.raises(ValueError):
            reference.capsule_query((0, 0, 0), (1, 0, 0), radius)
    with pytest.raises(ValueError):
        reference.capsule_query((0, 0, 0), (1, 0, 0), 1, tolerance_mm=-1)


@pytest.mark.parametrize("reflected", [False, True])
def test_oblique_sheared_cell_face_distance(reflected):
    angle = .37
    rotation = np.array([[math.cos(angle), -math.sin(angle), 0],
                         [math.sin(angle), math.cos(angle), 0], [0, 0, 1.]])
    linear = rotation @ np.array([[-1.2 if reflected else 1.2, .4, .2],
                                  [0, 1.5, .3], [0, 0, .8]])
    affine = np.eye(4)
    affine[:3, :3], affine[:3, 3] = linear, [2, -3, 5]
    reference = VoxelCellReference([[0, 0, 0]], affine)
    normal = np.linalg.inv(linear).T[:, 0]
    normal /= np.linalg.norm(normal)
    face = affine[:3, 3] + linear @ [.5, 0, 0]
    for distance in (1e-6, .1, .4):
        point = face + distance * normal
        assert reference.segment_distance(point, point) == pytest.approx(distance, abs=1e-10)
        assert reference.capsule_query(point, point, distance).collides
    center = affine[:3, 3]
    assert reference.segment_distance(center, center) == 0


def test_conservative_backend_has_no_false_feasible_for_sheared_cells(tmp_path):
    mask = np.zeros((9, 9, 9), dtype=np.uint8)
    mask[4, 4, 4] = 1
    path = tmp_path / "cell.npy"
    np.save(path, mask)
    affine = np.eye(4)
    affine[:3, :3] = [[1, .4, .1], [.2, 1.3, .2], [0, .1, .9]]
    reference = VoxelCellReference(np.argwhere(mask), affine)
    backend = VoxelMaskBackend(VoxelGeometry(uri=str(path), affine=affine.tolist()))
    rng = np.random.default_rng(9123)
    exact_collisions = conservative_only = 0
    for _ in range(64):
        endpoints = rng.uniform(2.3, 5.7, (2, 3)) @ affine[:3, :3].T
        radius = float(rng.uniform(0, .45))
        exact = reference.capsule_query(*endpoints, radius)
        bounded = backend.capsule_clearance(*endpoints, radius)
        assert not bounded.out_of_fov
        assert bounded.clearance_mm <= exact.centerline_distance_mm - radius + 1e-9
        if exact.collides:
            exact_collisions += 1
            assert bounded.clearance_mm <= 1e-9
        conservative_only += not exact.collides and bounded.clearance_mm <= 0
    assert exact_collisions > 0
    assert conservative_only > 0
