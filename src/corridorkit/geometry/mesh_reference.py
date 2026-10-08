"""Standalone, exhaustive triangle reference for *synthetic voxel-cell solids*.

This does not load anatomical meshes, infer tissue, compute signed distance, or
replace the production conservative backend. All distances are Euclidean in the
supplied physical coordinates. Cells are closed affine images of index cubes;
oblique rotations, reflections, anisotropy and shear are supported.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np


def _array(value, shape, name):
    result = np.asarray(value, dtype=float)
    if result.shape != shape or not np.all(np.isfinite(result)):
        raise ValueError(f"{name} must have shape {shape} and finite values")
    return result


def _radius(value, name="radius_mm"):
    value = float(value)
    if not np.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return value


def _point_segment_squared(point, start, end):
    delta = end - start
    denominator = float(delta @ delta)
    fraction = np.clip((point - start) @ delta / denominator, 0, 1) if denominator else 0.
    residual = point - start - fraction * delta
    return float(residual @ residual)


def _segment_segment_squared(start, end, first, second):
    # Enumerate the interior stationary point and all four boundary minima of
    # this box-constrained quadratic. Independent of the production Ericson code.
    matrix = np.column_stack((end - start, first - second))
    rhs = first - start
    parameters, _, rank, _ = np.linalg.lstsq(matrix, rhs, rcond=None)
    candidates = [
        _point_segment_squared(start, first, second),
        _point_segment_squared(end, first, second),
        _point_segment_squared(first, start, end),
        _point_segment_squared(second, start, end),
    ]
    if rank == 2 and np.all(parameters >= 0) and np.all(parameters <= 1):
        residual = matrix @ parameters - rhs
        candidates.append(float(residual @ residual))
    return min(candidates)


def _inside_triangle(point, triangle):
    basis = np.column_stack((triangle[1] - triangle[0], triangle[2] - triangle[0]))
    coordinates, _, rank, _ = np.linalg.lstsq(basis, point - triangle[0], rcond=None)
    return bool(rank == 2 and np.all(coordinates >= -1e-12)
                and coordinates.sum() <= 1 + 1e-12)


def _point_triangle_squared(point, triangle):
    first, second, third = triangle
    normal = np.cross(second - first, third - first)
    norm_squared = float(normal @ normal)
    edges = [(first, second), (second, third), (third, first)]
    best = min(_point_segment_squared(point, a, b) for a, b in edges)
    if norm_squared:
        signed_numerator = float((point - first) @ normal)
        projected = point - normal * signed_numerator / norm_squared
        if _inside_triangle(projected, triangle):
            best = min(best, signed_numerator ** 2 / norm_squared)
    return best


def segment_triangle_distance(start, end, triangle) -> float:
    """Nonnegative segment-to-closed-triangle distance, including degeneracies.

    A triangle is a surface, not a solid. Degenerate triangles reduce to their
    edges/vertices. Plane intersection plus boundary features suffices for two
    convex primitives; there is no centerline discretization.
    """
    start = _array(start, (3,), "start")
    end = _array(end, (3,), "end")
    triangle = _array(triangle, (3, 3), "triangle")
    normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
    denominator = float((end - start) @ normal)
    if denominator:
        fraction = float((triangle[0] - start) @ normal) / denominator
        if 0 <= fraction <= 1 and _inside_triangle(start + fraction * (end - start), triangle):
            return 0.
    squared = min(
        _point_triangle_squared(start, triangle),
        _point_triangle_squared(end, triangle),
        *(_segment_segment_squared(start, end, triangle[i], triangle[(i + 1) % 3])
          for i in range(3)),
    )
    return float(np.sqrt(max(0., squared)))


def segment_mesh_distance(start, end, triangles) -> float:
    """Distance to triangle *surfaces*, without any closed-mesh inside inference.

    Empty meshes are rejected: infinity is not a finite geometric distance.
    Use ``VoxelCellReference`` when solid-cell interior semantics are required.
    """
    start = _array(start, (3,), "start")
    end = _array(end, (3,), "end")
    triangles = np.asarray(triangles, dtype=float)
    if (triangles.ndim != 3 or triangles.shape[1:] != (3, 3)
            or len(triangles) == 0 or not np.all(np.isfinite(triangles))):
        raise ValueError("triangles must be a nonempty finite (n, 3, 3) array")
    return min(segment_triangle_distance(start, end, triangle) for triangle in triangles)


def triangulate_voxel_cells(indices, affine) -> np.ndarray:
    """Return twelve physical-space triangles per occupied, index-centered cell."""
    indices = np.asarray(indices, dtype=float)
    if (indices.ndim != 2 or indices.shape[1:] != (3,) or len(indices) == 0
            or not np.all(np.isfinite(indices)) or not np.all(indices == np.rint(indices))):
        raise ValueError("indices must be a nonempty finite integer-valued (n, 3) array")
    affine = _array(affine, (4, 4), "affine")
    if not np.allclose(affine[3], [0, 0, 0, 1], rtol=0, atol=1e-12):
        raise ValueError("affine must have homogeneous last row")
    if np.linalg.svd(affine[:3, :3], compute_uv=False).min() < 1e-12:
        raise ValueError("affine must have an invertible spatial transform")
    corners = np.asarray(list(product((-.5, .5), repeat=3)))
    # Corner ordering is lexicographic x,y,z. Winding is immaterial for distance.
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
             (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    triangle_ids = np.asarray([(a, b, c) for a, b, c, d in faces]
                              + [(a, c, d) for a, b, c, d in faces])
    vertices = (indices[:, None, :] + corners) @ affine[:3, :3].T + affine[:3, 3]
    return vertices[:, triangle_ids].reshape(-1, 3, 3)


@dataclass(frozen=True)
class MeshCapsuleResult:
    centerline_distance_mm: float
    collides: bool
    radius_mm: float
    tolerance_mm: float


class VoxelCellReference:
    """Exhaustive unsigned distance to the union of transformed closed cells.

    O(number of cells) slab checks and triangle scans: intended only for small
    validation phantoms or isolated near-boundary refinement, not clinical masks.
    There is deliberately no FOV policy here; empty space outside a mask's image
    extent must not be interpreted as known anatomy by callers.
    """

    def __init__(self, indices, affine):
        self.triangles = triangulate_voxel_cells(indices, affine)
        self.indices = np.array(indices, dtype=float, copy=True)
        self.affine = np.array(affine, dtype=float, copy=True)
        self.inverse = np.linalg.inv(self.affine)

    def segment_distance(self, start, end) -> float:
        start = _array(start, (3,), "start")
        end = _array(end, (3,), "end")
        index_start = self.inverse[:3, :3] @ start + self.inverse[:3, 3]
        index_end = self.inverse[:3, :3] @ end + self.inverse[:3, 3]
        delta = index_end - index_start
        for index in self.indices:
            lower, upper = 0., 1.
            for axis in range(3):
                if delta[axis] == 0:
                    if abs(index_start[axis] - index[axis]) > .5:
                        lower, upper = 1., 0.
                        break
                else:
                    a = (index[axis] - .5 - index_start[axis]) / delta[axis]
                    b = (index[axis] + .5 - index_start[axis]) / delta[axis]
                    lower, upper = max(lower, min(a, b)), min(upper, max(a, b))
            if lower <= upper:
                return 0.
        return segment_mesh_distance(start, end, self.triangles)

    def capsule_query(self, start, end, radius_mm, *, tolerance_mm=1e-9) -> MeshCapsuleResult:
        """Touching counts as collision. No penetration depth or signed distance."""
        radius = _radius(radius_mm)
        tolerance = _radius(tolerance_mm, "tolerance_mm")
        distance = self.segment_distance(start, end)
        return MeshCapsuleResult(distance, distance <= radius + tolerance, radius, tolerance)
