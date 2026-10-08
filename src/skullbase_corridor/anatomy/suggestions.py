"""Deterministic CT threshold suggestions, not anatomical segmentations."""
from __future__ import annotations

import numpy as np
from scipy import ndimage


def suggest_ct_masks(data, affine, *, bone_hu=300.0, air_hu=-500.0,
                     minimum_component_mm3=0.0) -> dict:
    array = np.asarray(data)
    matrix = np.asarray(affine, dtype=float)
    if array.ndim != 3 or not np.isfinite(array).all():
        raise ValueError("finite three-dimensional CT is required")
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("finite affine required")
    voxel_volume = abs(np.linalg.det(matrix[:3, :3]))
    if voxel_volume <= 0 or not np.allclose(matrix[3], [0, 0, 0, 1]):
        raise ValueError("invalid affine")
    if not np.isfinite([bone_hu, air_hu, minimum_component_mm3]).all():
        raise ValueError("thresholds must be finite")
    if bone_hu <= air_hu or minimum_component_mm3 < 0:
        raise ValueError("invalid thresholds")
    masks = {"bone": array >= bone_hu, "air": array <= air_hu}
    if minimum_component_mm3 > 0:
        for name, mask in masks.items():
            components, _ = ndimage.label(mask)
            sizes = np.bincount(components.ravel()) * voxel_volume
            keep = sizes >= minimum_component_mm3
            keep[0] = False
            masks[name] = keep[components]
    return {
        "masks": masks,
        "review_status": "required",
        "parameters": {"bone_hu": bone_hu, "air_hu": air_hu,
                       "minimum_component_mm3": minimum_component_mm3},
        "limitations": [
            "Requires calibrated Hounsfield units; verify imported intensities.",
            "Bone threshold misses thin/partial-volume bone and can include artifacts.",
            "Air threshold includes exterior air; it does not define a surgical corridor.",
            "No carotid, nerve, dura, tumor, or safe removal anatomy is inferred.",
        ],
    }
