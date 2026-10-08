"""Auditable eligibility checks for public CT/label collections."""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np

EXPECTED_NASALSEG_LABELS = {0, 1, 2, 3, 4, 5}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_files(directory: Path) -> list[Path]:
    suffixes = (".nii", ".nii.gz", ".nrrd", ".nhdr")
    return sorted(path for path in directory.rglob("*") if path.name.lower().endswith(suffixes))


def _stem(path: Path) -> str:
    name = path.name
    for suffix in (".nii.gz", ".nii", ".nrrd", ".nhdr"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _load(path: Path) -> tuple[np.ndarray, np.ndarray]:
    if path.name.lower().endswith((".nii", ".nii.gz")):
        image = nib.load(path)
        return np.asanyarray(image.dataobj), np.asarray(image.affine, dtype=float)
    try:
        import SimpleITK as sitk
    except ImportError as exc:
        raise RuntimeError("SimpleITK is required to audit NRRD data") from exc
    image = sitk.ReadImage(str(path))
    data = sitk.GetArrayFromImage(image).transpose(2, 1, 0)
    direction = np.asarray(image.GetDirection()).reshape(3, 3)
    affine = np.eye(4)
    affine[:3, :3] = direction @ np.diag(image.GetSpacing())
    affine[:3, 3] = image.GetOrigin()
    # SimpleITK physical coordinates are LPS; nibabel NIfTI affines are RAS.
    return data, np.diag([-1.0, -1.0, 1.0, 1.0]) @ affine


def case_id(path: Path) -> str:
    """Strip only documented role suffixes; identifiers remain case-sensitive."""
    value = _stem(path)
    for suffix in ("_image", "-image", "_img", "-img", "_label", "-label", "_seg", "-seg"):
        if value.lower().endswith(suffix):
            return value[: -len(suffix)]
    return value


def index_cases(paths: list[Path]) -> dict[str, Path]:
    indexed: dict[str, Path] = {}
    for path in paths:
        identifier = case_id(path)
        if identifier in indexed:
            raise ValueError(f"duplicate case ID {identifier!r}: {indexed[identifier]} and {path}")
        indexed[identifier] = path
    return indexed


def _pair(images: list[Path], labels: list[Path]) -> list[tuple[Path, Path]]:
    image_ids, label_ids = index_cases(images), index_cases(labels)
    return [(image_ids[key], label_ids[key]) for key in sorted(image_ids.keys() & label_ids.keys())]


def inspect_arrays(
    image: np.ndarray, image_affine: np.ndarray,
    label: np.ndarray, label_affine: np.ndarray,
    expected_labels: set[int] = EXPECTED_NASALSEG_LABELS,
) -> dict[str, Any]:
    """Integrity and physical-coverage fields, not anatomical approval."""
    finite = bool(np.all(np.isfinite(image)) and np.all(np.isfinite(label)))
    integral = bool(np.all(np.isfinite(label)) and np.all(label == np.floor(label)))
    values = sorted(int(value) for value in np.unique(label)) if integral else []
    affine_valid = all(
        affine.shape == (4, 4) and np.all(np.isfinite(affine))
        and np.allclose(affine[3], [0, 0, 0, 1], atol=1e-8, rtol=0)
        and abs(np.linalg.det(affine[:3, :3])) > 1e-12
        for affine in (image_affine, label_affine)
    )
    geometry_match = bool(
        affine_valid and image.ndim == label.ndim == 3 and image.shape == label.shape
        and np.allclose(image_affine, label_affine, atol=1e-4, rtol=0)
    )
    label_valid = integral and set(values) <= expected_labels and bool(set(values) - {0})
    reasons = []
    for valid, reason in (
        (finite, "nonfinite_data"), (affine_valid, "invalid_affine"),
        (geometry_match, "image_label_physical_geometry_mismatch"),
        (label_valid, "invalid_or_empty_labels"),
    ):
        if not valid:
            reasons.append(reason)
    result: dict[str, Any] = {
        "shape": list(image.shape), "label_shape": list(label.shape),
        "coordinate_frame": "RAS_mm", "finite": finite,
        "geometry_match": geometry_match, "affine_valid": bool(affine_valid),
        "image_affine_ras": image_affine.tolist(), "label_affine_ras": label_affine.tolist(),
        "labels_integral": integral, "labels_present": values, "labels_valid": bool(label_valid),
        "engineering_eligible": not reasons, "exclusion_reasons": reasons,
        "surgical_corridor_ground_truth": False,
        "expert_anatomical_review": "incomplete",
        "full_craniofacial_coverage": "not_established_cropped_airspace_volume",
        "limitations": [
            "Cropped nasal/maxillary airspace data are not full craniofacial imaging.",
            "Voxel spacing and header geometry are not verified measurement accuracy.",
            "No tumor, carotid, optic, petroclival target, or surgical access ground truth.",
            "Image intensity scale is unverified; values are not asserted to be HU.",
        ],
    }
    if image.ndim == 3 and affine_valid:
        spacing = np.linalg.norm(image_affine[:3, :3], axis=0)
        corners = np.asarray(list(itertools.product(*[(-0.5, n - 0.5) for n in image.shape])))
        physical = corners @ image_affine[:3, :3].T + image_affine[:3, 3]
        result.update({
            "spacing_mm": spacing.tolist(),
            "thin_slice_eligible": bool(np.max(spacing) <= 2.0),
            "physical_axis_extent_mm": (spacing * image.shape).tolist(),
            "physical_fov_corners_ras_mm": physical.tolist(),
            "physical_fov_aabb_ras_mm": [physical.min(axis=0).tolist(), physical.max(axis=0).tolist()],
            "voxel_volume_mm3": float(abs(np.linalg.det(image_affine[:3, :3]))),
        })
    finite_image = image[np.isfinite(image)]
    result["intensity"] = {
        "dtype": str(image.dtype), "scale": "unverified_not_assumed_HU",
        "finite_voxel_count": int(finite_image.size),
        "nonfinite_voxel_count": int(image.size - finite_image.size),
        "percentiles_0_1_50_99_100": (
            np.percentile(finite_image, [0, 1, 50, 99, 100]).tolist()
            if finite_image.size else None
        ),
        "constant": bool(finite_image.size and np.min(finite_image) == np.max(finite_image)),
    }
    if label.ndim == 3 and integral:
        boundary = np.zeros(label.shape, dtype=bool)
        for axis in range(3):
            for index in (0, -1):
                selection = [slice(None)] * 3
                selection[axis] = index
                boundary[tuple(selection)] = True
        result["label_coverage"] = []
        for value in values:
            if value == 0:
                continue
            indices = np.argwhere(label == value)
            touches = int(np.count_nonzero((label == value) & boundary))
            result["label_coverage"].append({
                "label": value, "voxel_count": len(indices),
                "index_bounds_inclusive": [indices.min(axis=0).tolist(), indices.max(axis=0).tolist()],
                "boundary_voxel_count": touches,
                "possible_label_clipping": touches > 0,
            })
    return result


def audit_pair(image_path: Path, label_path: Path) -> dict[str, Any]:
    if case_id(image_path) != case_id(label_path):
        raise ValueError("image and label must have exactly matching case IDs")
    record = {
        "case_id": case_id(image_path), "image": str(image_path), "label": str(label_path),
        "image_sha256": _sha256(image_path), "label_sha256": _sha256(label_path),
    }
    try:
        image, image_affine = _load(image_path)
        label, label_affine = _load(label_path)
        record.update(inspect_arrays(image, image_affine, label, label_affine))
    except Exception as exc:
        record.update(engineering_eligible=False, exclusion_reasons=["read_error"], error=str(exc))
    return record


def audit_dataset(
    images_directory: str | Path,
    labels_directory: str | Path,
    *,
    expected_labels: set[int] = EXPECTED_NASALSEG_LABELS,
    source_name: str = "NasalSeg",
    source_version: str = "unknown",
    source_license: str = "CC-BY-4.0",
    sample_limit: int | None = None,
) -> dict[str, Any]:
    """Audit geometry and labels without claiming surgical suitability."""
    image_paths = _image_files(Path(images_directory))
    label_paths = _image_files(Path(labels_directory))
    image_ids, label_ids = index_cases(image_paths), index_cases(label_paths)
    pairs = _pair(image_paths, label_paths)
    total_pairs = len(pairs)
    if sample_limit is not None:
        if isinstance(sample_limit, bool) or not isinstance(sample_limit, int) or sample_limit < 1:
            raise ValueError("sample_limit must be a positive integer")
        pairs = pairs[:sample_limit]
    records: list[dict[str, Any]] = []
    for image_path, label_path in pairs:
        record: dict[str, Any] = {
            "case_id": case_id(image_path),
            "image": str(image_path),
            "label": str(label_path),
        }
        try:
            image, image_affine = _load(image_path)
            label, label_affine = _load(label_path)
            record.update(inspect_arrays(image, image_affine, label, label_affine, expected_labels))
            record.update(image_sha256=_sha256(image_path), label_sha256=_sha256(label_path))
        except Exception as exc:
            record.update({"engineering_eligible": False, "exclusion_reasons": ["read_error"], "error": str(exc)})
        records.append(record)
    unmatched = []
    for identifier in sorted(image_ids.keys() ^ label_ids.keys()):
        unmatched.append({
            "case_id": identifier, "engineering_eligible": False,
            "exclusion_reasons": ["missing_label" if identifier in image_ids else "missing_image"],
            "image": str(image_ids[identifier]) if identifier in image_ids else None,
            "label": str(label_ids[identifier]) if identifier in label_ids else None,
        })
    records.extend(unmatched)
    return {
        "schema_version": "2.0",
        "source": {
            "name": source_name,
            "version": source_version,
            "license": source_license,
        },
        "image_file_count": len(image_paths),
        "label_file_count": len(label_paths),
        "paired_count": total_pairs,
        "audited_pair_count": len(pairs),
        "unmatched_count": len(unmatched),
        "unaudited_pair_ids": [case_id(image) for image, _ in _pair(image_paths, label_paths)[len(pairs):]],
        "engineering_eligible_count": sum(
            bool(record.get("engineering_eligible")) for record in records
        ),
        "records": records,
        "interpretation": (
            "Engineering eligibility does not establish complete operative anatomy, "
            "clinical validity, or suitability for patient-specific planning."
        ),
    }


def write_audit(path: str | Path, audit: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(audit, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
