"""Versioned TotalSegmentator output adapter for corridor planning.

The adapter never treats a model output as reviewed anatomy.  It maps known
specialist-task filenames onto the common CT grid and derives bilateral masks
only when physical-space connected components provide unambiguous evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
from scipy import ndimage

from skullbase_corridor.anatomy.entry_proposals import AnatomyMask
from skullbase_corridor.io.volumes import Volume, load_volume, resample_to_grid, validate_labels


MODEL_NAME = "TotalSegmentator"
MODEL_VERSION = "2.11.0"
REQUIRED_TASKS = (
    "craniofacial_structures",
    "head_glands_cavities",
    "headneck_bones_vessels",
)

_BONE_LABELS = (
    "skull",
    "mandible",
    "maxilla",
    "zygomatic_arch_left",
    "zygomatic_arch_right",
)
_ALIASES = {
    "nasal_cavity": ("nasal_cavity",),
    "left_nasal_cavity": ("nasal_cavity_left", "left_nasal_cavity"),
    "right_nasal_cavity": ("nasal_cavity_right", "right_nasal_cavity"),
    "maxillary_sinus": ("sinus_maxillary", "maxillary_sinus"),
    "hard_palate": ("hard_palate",),
    "upper_teeth": ("teeth_upper",),
    "left_orbit": ("eye_left", "orbit_left"),
    "right_orbit": ("eye_right", "orbit_right"),
    "left_ica": ("internal_carotid_artery_left",),
    "right_ica": ("internal_carotid_artery_right",),
    "left_optic_nerve": ("optic_nerve_left",),
    "right_optic_nerve": ("optic_nerve_right",),
}


@dataclass(frozen=True)
class AdapterResult:
    masks: Mapping[str, AnatomyMask]
    sources: Mapping[str, tuple[str, ...]]
    missing: tuple[str, ...]
    warnings: tuple[str, ...]
    model_name: str = MODEL_NAME
    model_version: str = MODEL_VERSION
    tasks: tuple[str, ...] = REQUIRED_TASKS


def _normal_name(path: Path) -> str:
    name = path.name.lower()
    for suffix in (".nii.gz", ".nii"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.replace("-", "_").replace(" ", "_")


def discover_outputs(directory: str | Path) -> dict[str, Path]:
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"TotalSegmentator output directory unavailable: {root}")
    result: dict[str, Path] = {}
    for path in sorted(root.rglob("*.nii")) + sorted(root.rglob("*.nii.gz")):
        name = _normal_name(path)
        if name in result and result[name] != path:
            raise ValueError(f"ambiguous TotalSegmentator output {name!r}")
        result[name] = path
    if not result:
        raise ValueError("TotalSegmentator output directory contains no NIfTI masks")
    return result


def _on_reference(path: Path, reference: Volume) -> np.ndarray:
    volume = load_volume(path, assume_spatial_unit="mm")
    validate_labels(volume)
    if volume.data.shape != reference.data.shape or not np.allclose(
        volume.affine, reference.affine, atol=1e-4, rtol=0
    ):
        volume = resample_to_grid(volume, reference, labels=True)
    return np.asarray(volume.data != 0, dtype=bool)


def _first(index: Mapping[str, Path], names: tuple[str, ...]) -> Path | None:
    return next((index[name] for name in names if name in index), None)


def split_bilateral_mask(
    mask: np.ndarray,
    affine: np.ndarray,
    *,
    minimum_component_mm3: float = 20.0,
    midline_gap_mm: float = 0.5,
) -> tuple[np.ndarray, np.ndarray]:
    """Return left/right masks using RAS X, or abstain if separation is ambiguous."""

    data = np.asarray(mask, dtype=bool)
    matrix = np.asarray(affine, dtype=float)
    if data.ndim != 3 or matrix.shape != (4, 4) or not data.any():
        raise ValueError("a nonempty 3-D mask and 4x4 affine are required")
    voxel_volume = abs(float(np.linalg.det(matrix[:3, :3])))
    components, count = ndimage.label(data)
    candidates: list[tuple[float, np.ndarray]] = []
    for label in range(1, count + 1):
        component = components == label
        if float(component.sum()) * voxel_volume < minimum_component_mm3:
            continue
        indices = np.argwhere(component)
        ras_x = indices @ matrix[0, :3] + matrix[0, 3]
        candidates.append((float(np.median(ras_x)), component))
    if len(candidates) < 2:
        raise ValueError("bilateral derivation requires two physical-space components")
    candidates.sort(key=lambda item: item[0])
    centers = np.asarray([item[0] for item in candidates])
    gaps = np.diff(centers)
    split = int(np.argmax(gaps)) + 1
    if float(gaps[split - 1]) <= 2 * midline_gap_mm:
        raise ValueError("bilateral components have no reliable left-right separation")
    # RAS X increases from patient left to patient right. The scanner origin is
    # arbitrary and therefore must never be treated as anatomical midline.
    left = np.logical_or.reduce([item for _, item in candidates[:split]])
    right = np.logical_or.reduce([item for _, item in candidates[split:]])
    if np.logical_and(left, right).any() or not left.any() or not right.any():
        raise ValueError("invalid bilateral split")
    return left, right


def adapt_outputs(
    directory: str | Path,
    reference: Volume,
    *,
    model_version: str = MODEL_VERSION,
) -> AdapterResult:
    """Map specialist outputs to predicted canonical masks on ``reference``."""

    if model_version != MODEL_VERSION:
        raise ValueError(f"unsupported TotalSegmentator version {model_version!r}")
    index = discover_outputs(directory)
    arrays: dict[str, np.ndarray] = {}
    sources: dict[str, tuple[str, ...]] = {}
    missing: list[str] = []
    warnings: list[str] = []

    bone_paths = tuple(index[name] for name in _BONE_LABELS if name in index)
    if bone_paths:
        arrays["bone"] = np.logical_or.reduce([_on_reference(path, reference) for path in bone_paths])
        sources["bone"] = tuple(str(path) for path in bone_paths)
    else:
        missing.append("bone")

    for canonical, names in _ALIASES.items():
        path = _first(index, names)
        if path is not None:
            arrays[canonical] = _on_reference(path, reference)
            sources[canonical] = (str(path),)

    for source_name, left_name, right_name in (
        ("nasal_cavity", "left_nasal_cavity", "right_nasal_cavity"),
        ("maxillary_sinus", "left_maxillary_sinus", "right_maxillary_sinus"),
    ):
        if left_name in arrays and right_name in arrays:
            continue
        path = _first(index, _ALIASES[source_name])
        if path is None:
            missing.extend((left_name, right_name))
            continue
        combined = _on_reference(path, reference)
        try:
            left, right = split_bilateral_mask(combined, reference.affine)
        except ValueError as exc:
            missing.extend((left_name, right_name))
            warnings.append(f"{source_name} bilateral derivation abstained: {exc}")
        else:
            arrays[left_name], arrays[right_name] = left, right
            sources[left_name] = sources[right_name] = (str(path),)

    protected_names = tuple(
        name for name in ("left_ica", "right_ica", "left_optic_nerve", "right_optic_nerve")
        if name in arrays and arrays[name].any()
    )
    if protected_names:
        arrays["protected"] = np.logical_or.reduce([arrays[name] for name in protected_names])
        sources["protected"] = tuple(path for name in protected_names for path in sources[name])
    else:
        missing.append("protected")

    for required in (
        "left_nasal_cavity",
        "right_nasal_cavity",
        "left_maxillary_sinus",
        "right_maxillary_sinus",
        "bone",
    ):
        if required not in arrays and required not in missing:
            missing.append(required)
    masks = {
        name: AnatomyMask(data, status="predicted")
        for name, data in arrays.items()
        if data.any()
    }
    return AdapterResult(
        masks=masks,
        sources=sources,
        missing=tuple(dict.fromkeys(missing)),
        warnings=tuple(warnings),
        model_version=model_version,
    )
