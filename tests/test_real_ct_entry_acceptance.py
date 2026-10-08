import importlib.util
import json
from pathlib import Path
import sys

import nibabel as nib
import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "research/run_real_ct_entry_acceptance.py"
SPEC = importlib.util.spec_from_file_location("run_real_ct_entry_acceptance", SCRIPT)
assert SPEC and SPEC.loader
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def _save(path, data, affine=None):
    affine = np.eye(4) if affine is None else affine
    image = nib.Nifti1Image(np.asarray(data), affine)
    image.set_qform(affine, code=1)
    image.set_sform(affine, code=1)
    image.header.set_xyzt_units("mm")
    nib.save(image, path)


def _inputs(tmp_path, *, include_ica=False, include_bone=True):
    shape = (31, 31, 21)
    ct = np.zeros(shape, dtype=np.int16)
    labels = np.zeros(shape, dtype=np.uint8)
    labels[3:9, 3:15, 7:14] = 1
    labels[22:28, 3:15, 7:14] = 2
    labels[10:14, 3:18, 8:13] = 3
    labels[17:21, 3:18, 8:13] = 4
    labels[13:18, 17:28, 8:16] = 5
    ct_path, label_path = tmp_path / "ct.nii.gz", tmp_path / "labels.nii.gz"
    _save(ct_path, ct)
    _save(label_path, labels)
    ts = tmp_path / "totalsegmentator"
    (ts / "craniofacial_structures").mkdir(parents=True)
    (ts / "headneck_bones_vessels").mkdir()
    if include_bone:
        bone = np.zeros(shape, dtype=np.uint8)
        bone[2, 3:15, 7:14] = 1
        bone[28, 3:15, 7:14] = 1
        _save(ts / "craniofacial_structures/skull.nii.gz", bone)
    if include_ica:
        left = np.zeros(shape, dtype=np.uint8)
        right = np.zeros(shape, dtype=np.uint8)
        left[8, 20:25, 10] = 1
        right[22, 20:25, 10] = 1
        _save(
            ts / "headneck_bones_vessels/internal_carotid_artery_left.nii.gz",
            left,
        )
        _save(
            ts / "headneck_bones_vessels/internal_carotid_artery_right.nii.gz",
            right,
        )
    return ct_path, label_path, ts


def test_declared_target_is_inside_posterior_superior_nasopharynx():
    mask = np.zeros((9, 12, 10), dtype=bool)
    mask[2:7, 2:10, 2:9] = True
    target = runner.select_declared_target(mask, np.eye(4))

    voxel = tuple(int(value) for value in target)
    assert mask[voxel]
    assert target[1] <= np.quantile(np.argwhere(mask)[:, 1], 0.35)
    assert target[2] >= np.quantile(np.argwhere(mask)[:, 2], 0.65)


def test_end_to_end_writes_replay_masks_cases_hashes_and_unavailable_ica(tmp_path):
    ct, labels, ts = _inputs(tmp_path)
    output = tmp_path / "acceptance"

    report_path = runner.run_acceptance(
        ct,
        labels,
        ts,
        output,
        case_id="public-PTEST",
        config=runner.RunnerConfig(candidates_per_surface=1),
    )
    report = json.loads(report_path.read_text())

    assert report["public_individual_ct"] is True
    assert report["synthetic"] is False
    assert report["averaged_atlas"] is False
    assert report["safety_claim"] is False
    assert report["replay"]["nasalseg_label_mapping"] == {
        "1": "right_maxillary_sinus",
        "2": "left_maxillary_sinus",
        "3": "right_nasal_cavity",
        "4": "left_nasal_cavity",
        "5": "nasopharynx",
    }
    assert report["proposal"]["candidates"]
    assert len(report["exact_paths"]) == len(report["proposal"]["candidates"])
    assert {item["state"] for item in report["exact_paths"]} == {"unavailable"}
    assert report["knowledge"]["ica_union"] == "unavailable"
    assert "left internal carotid artery" in report["knowledge"]["unavailable_or_conditional"]
    assert report["stage_timings_seconds_observational_not_replay_hash"]
    assert (output / "target_landmark_NOT_TUMOR.nii.gz").is_file()
    assert (output / "left_nasal_cavity.nii.gz").is_file()
    assert (output / "request.json").is_file()
    manifest = json.loads((output / "sha256-manifest.json").read_text())
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])
    for item in report["cases"]:
        case = json.loads((output / item["case_artifact"]).read_text())
        assert case["case"]["target"]["source"].endswith("NOT TUMOR")
        assert case["case"]["approaches"][0]["instrument"]["length_mm"] == 200.0
        assert case["case"]["approaches"][0]["portal"]["radius_mm"] == 4.0


def test_predicted_ica_union_is_exported_but_never_promoted_to_reviewed(tmp_path):
    ct, labels, ts = _inputs(tmp_path, include_ica=True)
    output = tmp_path / "acceptance"

    report = json.loads(runner.run_acceptance(
        ct, labels, ts, output, case_id="public-PICA",
        config=runner.RunnerConfig(candidates_per_surface=1),
    ).read_text())

    assert report["knowledge"]["ica_union"] == "available_predicted"
    assert (output / "protected.nii.gz").is_file()
    assert all(
        candidate["source_statuses"].get("protected") != "reviewed"
        for candidate in report["proposal"]["candidates"]
    )
    assert all(result["state"] in {"blocked", "unavailable"} for result in report["exact_paths"])


def test_refuses_missing_nasalseg_labels_and_nonempty_output(tmp_path):
    ct, labels, ts = _inputs(tmp_path)
    bad = np.zeros((31, 31, 21), dtype=np.uint8)
    _save(labels, bad)
    with pytest.raises(ValueError, match="labels 1..5"):
        runner.run_acceptance(ct, labels, ts, tmp_path / "bad-output", case_id="bad")

    output = tmp_path / "occupied"
    output.mkdir()
    (output / "keep.txt").write_text("do not overwrite")
    with pytest.raises(FileExistsError, match="nonempty"):
        runner.run_acceptance(ct, labels, ts, output, case_id="bad")
