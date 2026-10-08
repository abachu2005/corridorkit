from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from corridorkit.anatomy.entry_proposals import propose_entry_candidates
from corridorkit.anatomy.totalsegmentator_adapter import (
    adapt_outputs,
    split_bilateral_mask,
)
from corridorkit.io.volumes import Volume


def _save(path: Path, data: np.ndarray, affine: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = nib.Nifti1Image(data.astype(np.uint8), affine)
    image.set_qform(affine, code=1)
    image.set_sform(affine, code=1)
    image.header.set_xyzt_units("mm")
    nib.save(image, path)


def _reference():
    affine = np.eye(4)
    affine[:3, 3] = (-20, -10, -10)
    return Volume(np.zeros((41, 31, 21), dtype=np.int16), affine, "HU")


def _bilateral(shape, left, right):
    data = np.zeros(shape, dtype=np.uint8)
    data[left] = 1
    data[right] = 1
    return data


def test_physical_split_uses_ras_not_voxel_axis_direction():
    mask = _bilateral((41, 11, 11), np.s_[5:11, 2:9, 2:9], np.s_[30:36, 2:9, 2:9])
    affine = np.eye(4)
    affine[0, 0], affine[0, 3] = -1, 20

    left, right = split_bilateral_mask(mask, affine)

    assert np.argwhere(left)[:, 0].mean() > np.argwhere(right)[:, 0].mean()


def test_split_does_not_assume_scanner_origin_is_anatomical_midline():
    mask = _bilateral((41, 11, 11), np.s_[5:11, 2:9, 2:9], np.s_[30:36, 2:9, 2:9])
    affine = np.eye(4)
    affine[0, 3] = 80

    left, right = split_bilateral_mask(mask, affine)

    assert np.argwhere(left)[:, 0].mean() < np.argwhere(right)[:, 0].mean()


def test_split_abstains_for_unsplittable_midline_mask():
    mask = np.zeros((21, 11, 11), dtype=bool)
    mask[8:13, 2:9, 2:9] = True
    affine = np.eye(4)
    affine[0, 3] = -10
    with pytest.raises(ValueError, match="two physical-space components"):
        split_bilateral_mask(mask, affine)


def test_adapter_maps_prediction_without_manual_labels(tmp_path):
    reference = _reference()
    shape, affine = reference.data.shape, reference.affine
    nasal = _bilateral(shape, np.s_[8:14, 3:15, 7:14], np.s_[27:33, 3:15, 7:14])
    sinus = _bilateral(shape, np.s_[3:11, 3:15, 6:15], np.s_[30:38, 3:15, 6:15])
    skull = np.zeros(shape, dtype=np.uint8)
    skull[2:4, 3:15, 6:15] = 1
    skull[37:39, 3:15, 6:15] = 1
    ica_left = np.zeros(shape, dtype=np.uint8)
    ica_left[12, 18:24, 10] = 1
    _save(tmp_path / "head_glands_cavities/nasal_cavity.nii.gz", nasal, affine)
    _save(tmp_path / "craniofacial_structures/sinus_maxillary.nii.gz", sinus, affine)
    _save(tmp_path / "craniofacial_structures/skull.nii.gz", skull, affine)
    _save(
        tmp_path / "headneck_bones_vessels/internal_carotid_artery_left.nii.gz",
        ica_left,
        affine,
    )

    result = adapt_outputs(tmp_path, reference)

    assert not result.missing
    assert result.model_version == "2.11.0"
    assert all(item.status == "predicted" for item in result.masks.values())
    assert {"left_nasal_cavity", "right_nasal_cavity", "protected"} <= result.masks.keys()
    proposal = propose_entry_candidates((10, 20, 0), result.masks, affine)
    assert proposal.candidates
    assert all(candidate.conditional_constraints for candidate in proposal.candidates)


def test_adapter_preserves_explicit_missing_anatomy(tmp_path):
    reference = _reference()
    skull = np.zeros(reference.data.shape, dtype=np.uint8)
    skull[1:3] = 1
    _save(tmp_path / "craniofacial_structures/skull.nii.gz", skull, reference.affine)

    result = adapt_outputs(tmp_path, reference)

    assert "left_nasal_cavity" in result.missing
    assert "right_maxillary_sinus" in result.missing
    assert "protected" in result.missing


def test_adapter_prefers_explicit_sided_nasal_outputs(tmp_path):
    reference = _reference()
    left = np.zeros(reference.data.shape, dtype=np.uint8)
    right = np.zeros_like(left)
    left[8:14, 3:15, 7:14] = 1
    right[27:33, 3:15, 7:14] = 1
    _save(
        tmp_path / "head_glands_cavities/nasal_cavity_left.nii.gz",
        left,
        reference.affine,
    )
    _save(
        tmp_path / "head_glands_cavities/nasal_cavity_right.nii.gz",
        right,
        reference.affine,
    )
    skull = np.zeros_like(left)
    skull[1:3] = 1
    _save(tmp_path / "craniofacial_structures/skull.nii.gz", skull, reference.affine)

    result = adapt_outputs(tmp_path, reference)

    assert "left_nasal_cavity" in result.masks
    assert "right_nasal_cavity" in result.masks
    assert "left_nasal_cavity" not in result.missing
