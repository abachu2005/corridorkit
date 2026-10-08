"""Rigid CT/MRI registration with explicit review diagnostics."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import SimpleITK as sitk
from skullbase_corridor.export.json import atomic_json_write, file_sha256


def _validate_image(image: sitk.Image) -> None:
    if image.GetDimension() != 3 or min(image.GetSize()) < 4:
        raise ValueError("registration requires nondegenerate 3D images")
    data = sitk.GetArrayViewFromImage(image)
    if not np.isfinite(data).all() or np.ptp(data) <= 0:
        raise ValueError("registration requires finite, nonconstant images")
    if not np.isfinite(image.GetSpacing()).all() or min(image.GetSpacing()) <= 0:
        raise ValueError("registration requires positive finite spacing")


def landmark_errors(transform: sitk.Transform, fixed_lps, moving_lps) -> dict:
    """Transform maps fixed CT physical LPS points to moving MRI physical LPS."""
    fixed = np.asarray(fixed_lps, dtype=float)
    moving = np.asarray(moving_lps, dtype=float)
    if fixed.shape != moving.shape or fixed.ndim != 2 or fixed.shape[1] != 3 or len(fixed) < 3:
        raise ValueError("at least three matched 3D landmarks are required")
    if not np.isfinite(fixed).all() or not np.isfinite(moving).all():
        raise ValueError("landmarks must be finite")
    if np.linalg.matrix_rank(fixed - fixed.mean(axis=0)) < 2:
        raise ValueError("review landmarks must not be collinear")
    mapped = np.asarray([transform.TransformPoint(tuple(p)) for p in fixed])
    errors = np.linalg.norm(mapped - moving, axis=1)
    return {"count": len(errors), "errors_mm": errors.tolist(),
            "rms_mm": float(np.sqrt(np.mean(errors ** 2))), "maximum_mm": float(errors.max())}


def review_registration(proposal_path: str | Path, *, reviewer: str, fixed_lps, moving_lps,
                        maximum_error_mm: float, overlays_reviewed: bool) -> dict:
    """Record an explicit bounded review, never auto-approve optimizer convergence."""
    import json
    path = Path(proposal_path)
    proposal = json.loads(path.read_text())
    if not reviewer.strip() or not overlays_reviewed:
        raise ValueError("reviewer identity and explicit overlay review are required")
    if not np.isfinite(maximum_error_mm) or maximum_error_mm <= 0:
        raise ValueError("a prespecified positive landmark tolerance is required")
    for role in ("fixed_ct", "moving_mri", "transform"):
        if file_sha256(Path(proposal[role])) != proposal[f"{role}_sha256"]:
            raise ValueError(f"{role} changed since registration")
    errors = landmark_errors(sitk.ReadTransform(proposal["transform"]), fixed_lps, moving_lps)
    proposal["review_status"] = "approved" if errors["maximum_mm"] <= maximum_error_mm else "rejected"
    proposal["review"] = {
        "reviewer": reviewer, "overlays_reviewed": True, "landmarks": errors,
        "maximum_error_mm": maximum_error_mm,
        "fixed_landmarks_lps_mm": np.asarray(fixed_lps).tolist(),
        "moving_landmarks_lps_mm": np.asarray(moving_lps).tolist(),
    }
    atomic_json_write(path, proposal)
    return proposal


def resample_reviewed_mri(proposal_path: str | Path, output_path: str | Path) -> None:
    import json
    proposal = json.loads(Path(proposal_path).read_text())
    if proposal["review_status"] != "approved":
        raise ValueError("registration has not passed explicit review")
    for role in ("fixed_ct", "moving_mri", "transform"):
        if file_sha256(Path(proposal[role])) != proposal[f"{role}_sha256"]:
            raise ValueError(f"{role} changed after review")
    fixed = sitk.ReadImage(proposal["fixed_ct"])
    moving = sitk.ReadImage(proposal["moving_mri"])
    resampled = sitk.Resample(moving, fixed, sitk.ReadTransform(proposal["transform"]),
                             sitk.sitkLinear, 0.0, sitk.sitkFloat32)
    sitk.WriteImage(resampled, str(output_path))


def register_rigid(
    fixed_ct: str | Path,
    moving_mri: str | Path,
    output_transform: str | Path,
    *,
    seed: int = 20261001,
) -> dict:
    """Register MRI to CT and save a transform; never auto-approve the result."""
    fixed = sitk.ReadImage(str(fixed_ct), sitk.sitkFloat32)
    moving = sitk.ReadImage(str(moving_mri), sitk.sitkFloat32)
    _validate_image(fixed)
    _validate_image(moving)
    initial = sitk.CenteredTransformInitializer(
        fixed,
        moving,
        sitk.Euler3DTransform(),
        sitk.CenteredTransformInitializerFilter.GEOMETRY,
    )
    method = sitk.ImageRegistrationMethod()
    method.SetMetricAsMattesMutualInformation(numberOfHistogramBins=50)
    method.SetMetricSamplingStrategy(method.RANDOM)
    method.SetMetricSamplingPercentage(0.15, seed)
    method.SetInterpolator(sitk.sitkLinear)
    method.SetOptimizerAsGradientDescent(
        learningRate=1.0,
        numberOfIterations=200,
        convergenceMinimumValue=1e-6,
        convergenceWindowSize=10,
    )
    method.SetOptimizerScalesFromPhysicalShift()
    method.SetShrinkFactorsPerLevel([4, 2, 1])
    method.SetSmoothingSigmasPerLevel([2, 1, 0])
    method.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
    method.SetInitialTransform(initial, inPlace=False)
    final = method.Execute(fixed, moving)
    sitk.WriteTransform(final, str(output_transform))
    matrix = np.asarray(final.GetParameters(), dtype=float)
    proposal = {
        "fixed_ct": str(Path(fixed_ct).resolve()),
        "moving_mri": str(Path(moving_mri).resolve()),
        "transform": str(Path(output_transform).resolve()),
        "fixed_ct_sha256": file_sha256(Path(fixed_ct)),
        "moving_mri_sha256": file_sha256(Path(moving_mri)),
        "transform_sha256": file_sha256(Path(output_transform)),
        "transform_direction": "fixed_CT_LPS_to_moving_MRI_LPS",
        "seed": seed,
        "metric": "Mattes mutual information",
        "final_metric_value": float(method.GetMetricValue()),
        "optimizer_stop_condition": method.GetOptimizerStopConditionDescription(),
        "parameters": matrix.tolist(),
        "review_status": "required",
        "interpretation": (
            "The saved rigid transform is a registration proposal. Landmark and "
            "overlay review are required before measurements use it."
        ),
    }
    atomic_json_write(Path(output_transform).with_suffix(".registration.json"), proposal)
    return proposal
