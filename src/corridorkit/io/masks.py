"""Affine-preserving target extraction from labelled arrays."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

from corridorkit.domain.models import SampledMaskTarget


def voxel_volume_mm3(affine: ArrayLike) -> float:
    matrix = np.asarray(affine, dtype=float)
    if matrix.shape != (4, 4):
        raise ValueError("affine must be 4x4")
    if not np.all(np.isfinite(matrix)) or not np.allclose(matrix[3], [0, 0, 0, 1]):
        raise ValueError("affine must be finite with homogeneous last row")
    volume = abs(float(np.linalg.det(matrix[:3, :3])))
    if volume <= 1e-12:
        raise ValueError("affine has zero voxel volume")
    return volume


def indices_to_physical(indices: ArrayLike, affine: ArrayLike) -> NDArray[np.float64]:
    index_array = np.asarray(indices, dtype=float)
    matrix = np.asarray(affine, dtype=float)
    if index_array.ndim != 2 or index_array.shape[1] != 3:
        raise ValueError("indices must have shape (N, 3)")
    if matrix.shape != (4, 4):
        raise ValueError("affine must be 4x4")
    voxel_volume_mm3(matrix)
    if not np.all(np.isfinite(index_array)):
        raise ValueError("indices must be finite")
    homogeneous = np.column_stack((index_array, np.ones(len(index_array))))
    return (matrix @ homogeneous.T).T[:, :3]


def target_from_mask(
    mask: ArrayLike,
    affine: ArrayLike,
    *,
    stride: int = 1,
) -> SampledMaskTarget:
    data = np.asarray(mask)
    if data.ndim != 3:
        raise ValueError("mask must be three-dimensional")
    if not isinstance(stride, int) or stride < 1:
        raise ValueError("stride must be a positive integer")
    if not np.all(np.isfinite(data)):
        raise ValueError("mask must contain finite values")
    all_indices = np.argwhere(data.astype(bool))
    if not len(all_indices):
        raise ValueError("mask contains no foreground voxels")
    sampled = all_indices[::stride]
    physical = indices_to_physical(sampled, affine)
    matrix = np.asarray(affine, dtype=float)
    return SampledMaskTarget(
        points_mm=[tuple(float(x) for x in point) for point in physical],
        affine=tuple(tuple(float(x) for x in row) for row in matrix),
        source_shape=tuple(int(x) for x in data.shape),
        voxel_volume_mm3=voxel_volume_mm3(matrix),
        # Subsampling is not a validated volume estimator: omit the volume
        # rather than assigning stride voxels to the incomplete final block.
        point_volume_mm3=voxel_volume_mm3(matrix) if stride == 1 else None,
        source="mask_voxel_centers" if stride == 1 else "subsampled_mask_no_volume_estimate",
    )
