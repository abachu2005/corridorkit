from pathlib import Path

import numpy as np
import pytest
import SimpleITK as sitk

from skullbase_corridor.application.session import ReviewDocument
from skullbase_corridor.export.json import atomic_json_write, file_sha256, read_case, input_manifest
from skullbase_corridor.io.masks import target_from_mask
from skullbase_corridor.io.registration import landmark_errors, review_registration, resample_reviewed_mri
from skullbase_corridor.anatomy.suggestions import suggest_ct_masks
from skullbase_corridor.synthetic.cases import analytical_case


def test_review_invalidated_by_edit_and_roundtrip(tmp_path):
    document = ReviewDocument(case=analytical_case())
    with pytest.raises(ValueError):
        document.approve_anatomy("")
    document.approve_anatomy("Reviewer")
    assert document.anatomy_approved
    path = tmp_path / "case.json"
    document.save(path)
    assert read_case(path) == document.case
    loaded = ReviewDocument.model_validate_json(path.read_text())
    assert loaded.anatomy_approved
    document.replace_case(document.case.model_copy(update={"case_id": "edited"}))
    assert not document.anatomy_approved
    assert len(document.events) == 2


def test_subsampled_volume_never_overcounts():
    mask = np.ones((3, 3, 3), dtype=bool)
    exact = target_from_mask(mask, np.diag([2, 3, 4, 1]))
    assert len(exact.points_mm) * exact.point_volume_mm3 == pytest.approx(27 * 24)
    sampled = target_from_mask(mask, np.diag([2, 3, 4, 1]), stride=4)
    assert sampled.point_volume_mm3 is None
    for affine in (np.full((4, 4), np.nan), np.zeros((4, 4))):
        with pytest.raises(ValueError):
            target_from_mask(mask, affine)


def test_thresholds_are_review_required_and_physical_components():
    ct = np.zeros((4, 4, 4))
    ct[0, 0, 0] = 700
    ct[3, 3, 3] = -900
    result = suggest_ct_masks(ct, np.diag([2, 2, 2, 1]), minimum_component_mm3=9)
    assert not result["masks"]["bone"].any()
    assert not result["masks"]["air"].any()
    assert result["review_status"] == "required"
    assert "carotid" in " ".join(result["limitations"])


def test_registration_landmarks_direction_and_noncollinear():
    transform = sitk.TranslationTransform(3, [2, -3, 1])
    fixed = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0]])
    moving = fixed + [2, -3, 1]
    assert landmark_errors(transform, fixed, moving)["rms_mm"] == 0
    assert landmark_errors(transform, moving, fixed)["rms_mm"] > 0
    with pytest.raises(ValueError):
        landmark_errors(transform, np.zeros((3, 3)), np.zeros((3, 3)))


def test_registration_approval_hash_guard_and_resampling(tmp_path):
    fixed = tmp_path / "fixed.nii.gz"
    moving = tmp_path / "moving.nii.gz"
    transform = tmp_path / "transform.tfm"
    volume = sitk.GetImageFromArray(np.arange(125, dtype=np.float32).reshape(5, 5, 5))
    sitk.WriteImage(volume, str(fixed))
    sitk.WriteImage(volume, str(moving))
    sitk.WriteTransform(sitk.Euler3DTransform(), str(transform))
    proposal = tmp_path / "transform.registration.json"
    record = {"review_status": "required"}
    for key, path in [("fixed_ct", fixed), ("moving_mri", moving), ("transform", transform)]:
        record[key] = str(path)
        record[f"{key}_sha256"] = file_sha256(path)
    atomic_json_write(proposal, record)
    output = tmp_path / "resampled.nii.gz"
    with pytest.raises(ValueError, match="review"):
        resample_reviewed_mri(proposal, output)
    points = [[0, 0, 0], [3, 0, 0], [0, 3, 0]]
    reviewed = review_registration(proposal, reviewer="Reviewer", fixed_lps=points,
                                   moving_lps=points, maximum_error_mm=1, overlays_reviewed=True)
    assert reviewed["review_status"] == "approved"
    resample_reviewed_mri(proposal, output)
    assert np.allclose(sitk.GetArrayFromImage(sitk.ReadImage(str(output))),
                       sitk.GetArrayFromImage(volume))
    sitk.WriteTransform(sitk.TranslationTransform(3, [1, 0, 0]), str(transform))
    with pytest.raises(ValueError, match="changed"):
        resample_reviewed_mri(proposal, output)


def test_hash_mismatch_blocks_analysis(tmp_path):
    from skullbase_corridor.domain.models import CorridorCase
    path = tmp_path / "source.nii.gz"
    path.write_bytes(b"first")
    data = analytical_case().model_dump(mode="json")
    data["source_image"] = {"uri": str(path), "sha256": file_sha256(path)}
    case = CorridorCase.model_validate(data)
    assert input_manifest(case)[0]["status"] == "available"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        input_manifest(case)


def test_shareable_export_has_geometry_without_local_paths(tmp_path):
    from skullbase_corridor.domain.models import CorridorCase
    from skullbase_corridor.geometry.engine import analyze_case
    from skullbase_corridor.export.json import export_result
    path = tmp_path / "private-patient-name.npy"
    np.save(path, np.zeros((3, 3, 3)))
    data = analytical_case().model_dump(mode="json")
    data["source_image"] = {"uri": str(path)}
    case = CorridorCase.model_validate(data)
    result = analyze_case(case)
    output = tmp_path / "export.json"
    export_result(output, case, result)
    text = output.read_text()
    assert "private-patient-name" not in text
    assert str(tmp_path) not in text
    assert '"geometry"' in text


def test_anatomy_approval_binds_external_mask_bytes(tmp_path):
    from skullbase_corridor.domain.models import CorridorCase
    path = tmp_path / "mask.npy"
    np.save(path, np.zeros((10, 10, 10), np.uint8))
    data = analytical_case().model_dump(mode="json")
    data["protected_structures"] = [{
        "name": "reviewed-mask", "geometry": {"kind": "voxel", "uri": str(path),
                                            "affine": np.eye(4).tolist()}}]
    document = ReviewDocument(case=CorridorCase.model_validate(data))
    document.approve_anatomy("Reviewer")
    assert document.anatomy_approved
    np.save(path, np.ones((10, 10, 10), np.uint8))
    assert not document.anatomy_approved
