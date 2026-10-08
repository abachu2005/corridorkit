from itertools import permutations, product

import nibabel as nib
import numpy as np
import pytest
import SimpleITK as sitk

from corridorkit.desktop.editing import MaskEditor
from corridorkit.io.volumes import (
    Volume,
    inspect_dicom_series,
    load_volume,
    orthogonal_resample,
    validate_alignment,
)


def test_all_axis_permutations_and_flips_preserve_physical_values():
    for permutation in permutations(range(3)):
        for signs in product((-1, 1), repeat=3):
            affine = np.eye(4)
            affine[:3, :3] = np.eye(3)[:, permutation] @ np.diag(np.array(signs) * [1, 2, 3])
            affine[:3, 3] = [17, -9, 2]
            template = Volume(np.zeros((3, 4, 5)), affine)
            indices = np.indices(template.data.shape).reshape(3, -1).T
            world = template.index_to_world(indices)
            template.data = (world @ [1, 10, 100]).reshape(template.data.shape)
            display = orthogonal_resample(template)
            displayed_world = display.index_to_world(
                np.indices(display.data.shape).reshape(3, -1).T
            )
            np.testing.assert_allclose(
                display.data.ravel(), displayed_world @ [1, 10, 100], atol=0.001
            )
            assert np.all(np.diag(display.affine)[:3] > 0)


def test_oblique_resampling_samples_world_not_array_axes():
    angle = np.deg2rad(31)
    affine = np.eye(4)
    affine[:3, :3] = [
        [np.cos(angle), -2 * np.sin(angle), 0.2],
        [np.sin(angle), 2 * np.cos(angle), 0],
        [0, 0, 3],
    ]
    affine[:3, 3] = [5, -3, 20]
    volume = Volume(np.zeros((9, 8, 7)), affine)
    indices = np.indices(volume.data.shape).reshape(3, -1).T
    volume.data = (volume.index_to_world(indices) @ [3, -2, 0.4]).reshape(volume.data.shape)
    display = orthogonal_resample(volume)
    world = display.index_to_world(np.indices(display.data.shape).reshape(3, -1).T)
    finite = np.isfinite(display.data.ravel())
    assert finite.any() and (~finite).any()
    np.testing.assert_allclose(
        display.data.ravel()[finite], (world @ [3, -2, 0.4])[finite], atol=1e-4
    )
    np.testing.assert_allclose(
        volume.world_to_index(volume.index_to_world(indices)), indices, atol=1e-12
    )


def test_nifti_units_and_affine_conflicts(tmp_path):
    affine = np.diag([0.001, 0.002, 0.003, 1])
    image = nib.Nifti1Image(np.zeros((3, 4, 5), np.float32), affine)
    image.header.set_xyzt_units("meter")
    path = tmp_path / "meter.nii"
    nib.save(image, path)
    volume = load_volume(path)
    np.testing.assert_allclose(volume.spacing, [1, 2, 3])
    assert volume.intensity_unit == "unknown"
    path = tmp_path / "unknown.nii"
    image.header.set_xyzt_units("unknown")
    nib.save(image, path)
    with pytest.raises(ValueError, match="Unknown spatial units"):
        load_volume(path)
    assert load_volume(path, assume_spatial_unit="mm").metadata["spatial_units_assumed"]
    path = tmp_path / "conflicting-forms.nii"
    image.set_qform(np.eye(4), code=1)
    image.set_sform(affine, code=1)
    image.header.set_xyzt_units("mm")
    nib.save(image, path)
    with pytest.raises(ValueError, match="qform/sform"):
        load_volume(path)


def test_nrrd_lps_spacing_and_unknown_units(tmp_path):
    image = sitk.GetImageFromArray(np.arange(60, dtype=np.int16).reshape(3, 4, 5))
    image.SetSpacing((0.7, 0.8, 2))
    image.SetOrigin((10, 20, 30))
    path = tmp_path / "image.nrrd"
    sitk.WriteImage(image, str(path))
    with pytest.raises(ValueError, match="Unknown spatial units"):
        load_volume(path)
    volume = load_volume(path, assume_spatial_unit="mm")
    np.testing.assert_allclose(volume.index_to_world([1, 2, 1]), [-10.7, -21.6, 32])
    np.testing.assert_array_equal(volume.data, sitk.GetArrayFromImage(image).transpose(2, 1, 0))


def test_nrrd_explicit_ras_header(tmp_path):
    path = tmp_path / "ras.nrrd"
    path.write_bytes(
        b"NRRD0005\ntype: short\ndimension: 3\nspace: right-anterior-superior\n"
        b"sizes: 2 2 2\nspace directions: (2,0,0) (0,3,0) (0,0,4)\n"
        b'space units: "mm" "mm" "mm"\nspace origin: (10,20,30)\n'
        b"encoding: raw\nendian: little\n\n" + np.arange(8, dtype="<i2").tobytes()
    )
    volume = load_volume(path)
    np.testing.assert_allclose(volume.index_to_world([1, 1, 1]), [12, 23, 34])


def write_ct(directory, *, uid=None, positions=(0, 2, 4), **overrides):
    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

    uid = uid or generate_uid()
    study, frame = generate_uid(), generate_uid()
    for index, z in enumerate(positions):
        meta = FileMetaDataset()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.MediaStorageSOPClassUID = CTImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        ds = FileDataset(str(directory / f"{index}.dcm"), {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID, ds.SOPInstanceUID = CTImageStorage, meta.MediaStorageSOPInstanceUID
        ds.SeriesInstanceUID, ds.StudyInstanceUID, ds.FrameOfReferenceUID = uid, study, frame
        ds.Modality = "CT"
        ds.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
        ds.Rows, ds.Columns = 4, 5
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.ImagePositionPatient = [10, 20, z]
        ds.PixelSpacing = [0.8, 0.7]
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 1
        ds.RescaleSlope, ds.RescaleIntercept, ds.RescaleType = 2, -1000, "HU"
        for key, value in overrides.items():
            setattr(ds, key, value)
        ds.PixelData = np.full((4, 5), int(z + 100), dtype="<i2").tobytes()
        ds.save_as(ds.filename, enforce_file_format=True)
    return uid


def test_ct_requires_choice_and_sorts_physical_slices(tmp_path):
    uid = write_ct(tmp_path, positions=(4, 0, 2))
    info = inspect_dicom_series(tmp_path)
    assert len(info) == 1 and info[0].valid
    with pytest.raises(ValueError, match="explicit series_uid"):
        load_volume(tmp_path)
    volume = load_volume(tmp_path, series_uid=uid)
    assert volume.intensity_unit == "HU"
    np.testing.assert_array_equal(volume.data[0, 0], [-800, -796, -792])
    np.testing.assert_allclose(volume.index_to_world([1, 2, 1]), [-10.7, -21.6, 2])


@pytest.mark.parametrize(
    "positions,overrides",
    [
        ((0, 2, 5), {}),
        ((0, 0, 2), {}),
        ((0, 2, 4), {"ImageType": ["LOCALIZER"]}),
        ((0, 2, 4), {"Modality": "MR"}),
        ((0, 2, 4), {"NumberOfFrames": 3}),
        ((0, 2, 4), {"RescaleType": "US"}),
    ],
)
def test_ct_rejects_nonconventional_or_irregular_series(tmp_path, positions, overrides):
    uid = write_ct(tmp_path, positions=positions, **overrides)
    assert not inspect_dicom_series(tmp_path)[0].valid
    with pytest.raises(ValueError):
        load_volume(tmp_path, series_uid=uid)


def test_mask_alignment_and_mm_brush_label_preservation_undo():
    source = Volume(np.zeros((9, 9, 9)), np.diag([1, 2, 3, 1]))
    labels = np.zeros(source.data.shape, np.uint16)
    labels[4, 4, 4] = 7
    mask = Volume(labels, source.affine)
    editor = MaskEditor(source, mask, allowed_labels={2, 7})
    assert editor.paint([4, 8, 12], 2.1, 2) > 0
    assert editor.volume.data[4, 4, 4] == 7
    assert editor.volume.data[6, 4, 4] == 2
    assert editor.volume.data[4, 5, 4] == 2
    assert editor.volume.data[4, 4, 5] == 0  # 3 mm away, not 1 "voxel".
    painted = editor.volume.data.copy()
    assert editor.undo()
    np.testing.assert_array_equal(editor.volume.data, labels)
    assert editor.redo()
    np.testing.assert_array_equal(editor.volume.data, painted)
    editor.paint([4, 8, 12], 2.1, 2, erase=True)
    assert editor.volume.data[4, 4, 4] == 7
    with pytest.raises(ValueError, match="outside"):
        editor.paint([100, 8, 12], 2, 2)
    wrong = source.affine.copy()
    wrong[0, 3] = 1
    with pytest.raises(ValueError, match="does not match"):
        validate_alignment(source, Volume(labels, wrong))


def test_oblique_plane_brush_and_forbidden_anatomy():
    affine = np.eye(4)
    angle = np.pi / 4
    affine[:2, :2] = [[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]]
    source = Volume(np.zeros((7, 7, 7)), affine)
    forbidden = Volume(np.zeros_like(source.data), affine)
    forbidden.data[3, 3, 3] = 1
    editor = MaskEditor(source, forbidden=forbidden)
    centre = source.index_to_world([3, 3, 3])
    editor.paint(centre, 2, 4, plane_axis=2, plane_thickness_mm=0.5)
    assert editor.volume.data[3, 3, 3] == 0
    assert not editor.volume.data[:, :, :3].any()
    assert not editor.volume.data[:, :, 4:].any()
    report = editor.validate_for_analysis()
    assert report["anatomical_review"] == "required"
    assert report["volume_mm3"] == pytest.approx(report["voxel_count"])


def test_bool_masks_can_be_edited_without_collapsing_labels():
    source = Volume(np.zeros((3, 3, 3)), np.eye(4))
    editor = MaskEditor(source, Volume(np.zeros((3, 3, 3), bool), np.eye(4)))
    editor.paint([1, 1, 1], 0.2, 7)
    assert editor.volume.data[1, 1, 1] == 7


def test_dicom_mixed_frames_and_gantry_shear_rejected(tmp_path):
    import pydicom
    from pydicom.uid import generate_uid

    uid = write_ct(tmp_path)
    path = tmp_path / "1.dcm"
    middle = pydicom.dcmread(path)
    original_frame = middle.FrameOfReferenceUID
    middle.FrameOfReferenceUID = generate_uid()
    middle.save_as(path)
    with pytest.raises(ValueError, match="FrameOfReferenceUID"):
        load_volume(tmp_path, series_uid=uid)
    middle.FrameOfReferenceUID = original_frame
    middle.ImagePositionPatient = [11, 20, 2]
    middle.save_as(path)
    with pytest.raises(ValueError, match="gantry"):
        load_volume(tmp_path, series_uid=uid)
