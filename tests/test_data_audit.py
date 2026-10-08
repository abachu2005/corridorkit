from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from skullbase_corridor.data.audit import _load, _pair, audit_dataset, inspect_arrays


def save(path: Path, data: np.ndarray, affine: np.ndarray) -> None:
    nib.save(nib.Nifti1Image(data, affine), path)


def test_dataset_audit_accepts_matching_affine_and_known_labels(tmp_path):
    images, labels = tmp_path / "images", tmp_path / "labels"
    images.mkdir()
    labels.mkdir()
    affine = np.diag([0.6, 0.6, 1.5, 1.0])
    image = np.zeros((8, 9, 10), dtype=np.int16)
    label = np.zeros_like(image)
    label[2:4, 2:4, 2:4] = 1
    save(images / "case01.nii.gz", image, affine)
    save(labels / "case01.nii.gz", label, affine)
    audit = audit_dataset(images, labels, source_version="test")
    assert audit["paired_count"] == 1
    assert audit["engineering_eligible_count"] == 1
    assert audit["records"][0]["thin_slice_eligible"] is True
    assert audit["records"][0]["surgical_corridor_ground_truth"] is False


def test_dataset_audit_rejects_geometry_and_unknown_labels(tmp_path):
    images, labels = tmp_path / "images", tmp_path / "labels"
    images.mkdir()
    labels.mkdir()
    save(images / "case01.nii.gz", np.zeros((3, 3, 3)), np.eye(4))
    invalid = np.full((3, 3, 3), 9, dtype=np.uint8)
    save(labels / "case01.nii.gz", invalid, np.diag([2.0, 1.0, 1.0, 1.0]))
    audit = audit_dataset(images, labels)
    record = audit["records"][0]
    assert record["engineering_eligible"] is False
    assert record["geometry_match"] is False
    assert record["labels_valid"] is False


def test_pairing_never_zips_and_rejects_duplicates():
    assert _pair([Path("P001_img.nrrd")], [Path("P002_seg.nrrd")]) == []
    with pytest.raises(ValueError, match="duplicate case ID"):
        _pair([Path("P001_img.nrrd"), Path("other/P001_img.nrrd")], [])
    with pytest.raises(ValueError, match="duplicate case ID"):
        _pair([], [Path("P001_seg.nrrd"), Path("P001_label.nrrd")])


def test_unmatched_records_and_sampling_are_explicit(tmp_path):
    images, labels = tmp_path / "images", tmp_path / "labels"
    images.mkdir()
    labels.mkdir()
    save(images / "P001_img.nii.gz", np.zeros((3, 3, 3)), np.eye(4))
    save(labels / "P002_seg.nii.gz", np.ones((3, 3, 3)), np.eye(4))
    report = audit_dataset(images, labels)
    assert report["paired_count"] == 0
    assert report["unmatched_count"] == 2
    assert {row["exclusion_reasons"][0] for row in report["records"]} == {"missing_image", "missing_label"}
    with pytest.raises(ValueError):
        audit_dataset(images, labels, sample_limit=-1)


def test_nonintegral_labels_rejected_and_clipping_intensity_recorded():
    image = np.arange(64, dtype=float).reshape(4, 4, 4)
    labels = np.ones_like(image)
    report = inspect_arrays(image, np.eye(4), labels, np.eye(4))
    assert report["label_coverage"][0]["possible_label_clipping"]
    assert report["physical_axis_extent_mm"] == [4, 4, 4]
    assert report["intensity"]["percentiles_0_1_50_99_100"][0] == 0
    labels[0, 0, 0] = 1.5
    assert not inspect_arrays(image, np.eye(4), labels, np.eye(4))["labels_valid"]
    labels[0, 0, 0] = np.nan
    assert not inspect_arrays(image, np.eye(4), labels, np.eye(4))["engineering_eligible"]


def test_absolute_geometry_tolerance_not_scaled_by_large_origin():
    image = np.zeros((3, 3, 3))
    first = np.eye(4)
    first[0, 3] = 10000
    second = first.copy()
    second[0, 3] += 0.01
    report = inspect_arrays(image, first, np.ones_like(image), second)
    assert not report["geometry_match"]


def test_nrrd_lps_converts_to_nifti_ras(tmp_path):
    import SimpleITK as sitk
    data = np.zeros((3, 4, 5), dtype=np.int16)
    image = sitk.GetImageFromArray(data.transpose(2, 1, 0))
    image.SetSpacing((0.6, 0.7, 1.5))
    image.SetOrigin((10, 20, 30))
    path = tmp_path / "case.nrrd"
    sitk.WriteImage(image, str(path))
    loaded, affine = _load(path)
    assert np.array_equal(loaded, data)
    expected = np.diag([-0.6, -0.7, 1.5, 1])
    expected[:3, 3] = [-10, -20, 30]
    np.testing.assert_allclose(affine, expected)
