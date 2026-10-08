"""Real cached CT integration tests; never substitute a synthetic scan."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from skullbase_corridor.application.session import ReviewDocument
from skullbase_corridor.domain.models import AnalysisStatus, KnowledgeStatus, SampledMaskTarget
from skullbase_corridor.export.json import export_result, file_sha256, read_case
from skullbase_corridor.geometry.engine import analyze_case
from skullbase_corridor.io.masks import target_from_mask
from skullbase_corridor.io.volumes import load_volume, validate_alignment

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "research/prepare_interactive_real_case.py"
spec = importlib.util.spec_from_file_location("prepare_interactive_real_case", SCRIPT)
preparation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preparation)


@pytest.fixture(scope="module")
def real_case(tmp_path_factory):
    cache = ROOT / "research/data-cache/desktop-smoke"
    required = [cache / filename for filename in
                ("ct.nii.gz", "labels.nii.gz", "ct.nrrd", "labels.nrrd", "conversion.json")]
    required += [ROOT / "research/data-cache/NasalSeg-v2.zip",
                 ROOT / "research/data-cache/NasalSeg-v2-source.json"]
    if not all(path.is_file() for path in required):
        pytest.skip("Existing real NasalSeg P001 cache unavailable; no download or phantom fallback")
    path = preparation.write_interactive_real_case(tmp_path_factory.mktemp("real-scan"))
    return path, read_case(path), json.loads(path.read_text())["real_case_metadata"]


def test_actual_native_label_voxels_and_ct_reference(real_case):
    path, case, metadata = real_case
    ct = load_volume(case.source_image.uri)
    labels = load_volume(metadata["source_label_mask_uri"])
    roi = load_volume(path.parent / metadata["target_mask_uri"])
    validate_alignment(ct, labels)
    validate_alignment(ct, roi)
    expected = np.zeros(labels.data.shape, dtype=np.uint8)
    expected[31:38, 85:92, 21:28] = labels.data[31:38, 85:92, 21:28] == 1
    np.testing.assert_array_equal(roi.data, expected)
    assert np.count_nonzero(expected) == 343
    assert isinstance(case.target, SampledMaskTarget)
    assert case.target == target_from_mask(expected, labels.affine, stride=1)
    assert case.target.point_volume_mm3 == pytest.approx(0.5859375**2 * 1.5)
    assert case.target.source_shape == (153, 205, 52)
    assert case.source_image.modality == "CT"
    assert case.source_image.sha256 == file_sha256(Path(case.source_image.uri))
    assert Path(case.source_image.uri).is_absolute()
    assert Path(case.source_image.uri).parent != path.parent
    assert metadata["target_mask_sha256"] == file_sha256(path.parent / preparation.TARGET_FILENAME)
    assert sorted(item.name for item in path.parent.iterdir()) == sorted([
        preparation.CASE_FILENAME, preparation.TARGET_FILENAME, "README.md",
    ])


def test_explicit_provenance_and_unreviewed_safety_contract(real_case):
    path, case, metadata = real_case
    assert metadata["real_scan"] and not metadata["synthetic"]
    assert not metadata["patient_study"]
    assert not metadata["clinical_use"]
    assert not metadata["validated_anatomy"]
    assert not metadata["anatomy_reviewed"]
    assert not metadata["target_is_tumor"]
    assert not metadata["clearance_available"]
    assert "NOT TUMOR" in metadata["warning"]
    assert metadata["roi"]["stride"] == 1
    assert metadata["roi"]["resampled"] is False
    assert metadata["roi"]["crop_start_inclusive"] == [31, 85, 21]
    assert metadata["roi"]["crop_stop_exclusive"] == [38, 92, 28]
    provenance = metadata["provenance"]
    assert provenance["subject"] == "P001"
    assert provenance["partition"] == "development"
    assert provenance["declared_data_license"] == "CC-BY-4.0"
    assert provenance["source_record"]["sha256"] == preparation.ARCHIVE_SHA256
    assert provenance["source_members"] == {
        "ct": "images/P001_img.nrrd", "labels": "labels/P001_seg.nrrd",
    }
    assert "not grant redistribution rights" in provenance["redistribution"]
    assert "unknown" in provenance["intensity_units"]
    readme = (path.parent / "README.md").read_text()
    for value in ("NOT TUMOR", "CC-BY-4.0", "13893419", "343", "abstain", "attribution"):
        assert value in readme
    assert metadata["source_label_display_only"]
    assert not metadata["portals"]["reviewed"]
    assert not metadata["portals"]["anatomical_entry_claim"]
    for approach in case.approaches:
        assert "UNREVIEWED" in approach.name
        assert approach.portal.center_mm[1] > case.target.array()[:, 1].max()
        assert approach.nominal_direction[1] < 0
        assert not approach.allow_unknown_anatomy
        assert not approach.virtual_bone_removals
    assert all(item.status == KnowledgeStatus.UNKNOWN and item.geometry is None
               for item in case.protected_structures)
    names = " ".join(item.name.lower() for item in case.protected_structures)
    assert "carotid" in names and "cranial nerves" in names and "optic" in names


def test_engine_abstains_and_actual_save_export_apis(real_case, tmp_path):
    path, case, _ = real_case
    document = ReviewDocument(case=case)
    assert not document.anatomy_approved
    # Simulate erasing one native-grid slice in an editor, then import every
    # remaining voxel via the real API. Never call approve_anatomy.
    roi = load_volume(path.parent / preparation.TARGET_FILENAME)
    edited_mask = roi.data.copy()
    edited_mask[31, :, :] = 0
    edited_target = target_from_mask(edited_mask, roi.affine, stride=1)
    assert len(edited_target.points_mm) == 294
    document.replace_case(case.model_copy(update={"target": edited_target}),
                          detail="Demonstration ROI edit; NOT TUMOR")
    saved = tmp_path / "review.json"
    document.save(saved)
    reloaded = ReviewDocument.model_validate_json(saved.read_text())
    assert not reloaded.anatomy_approved
    assert read_case(saved) == reloaded.case
    result = analyze_case(reloaded.case, base_directory=path.parent)
    for approach in result.approaches:
        assert approach.status == AnalysisStatus.ABSTAINED
        assert approach.minimum_clearance_mm is None
        assert approach.reached_measure_mm3 is None
        assert approach.feasible_trajectory_count == 0
        assert not approach.trajectories
    exported = export_result(tmp_path / "result.json", reloaded.case, result,
                             base_directory=path.parent)
    assert all(item["status"] == "abstained" and item["minimum_clearance_mm"] is None
               for item in exported["result"]["approaches"])
    assert exported["input_files"][0]["status"] == "available"


def test_cli_reproduces_identical_case_and_roi(real_case, tmp_path):
    path, _, _ = real_case
    output = tmp_path / "cli"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    replay = output / preparation.CASE_FILENAME
    assert completed.stdout.strip() == str(replay)
    assert replay.read_bytes() == path.read_bytes()
    assert (output / preparation.TARGET_FILENAME).read_bytes() == (
        path.parent / preparation.TARGET_FILENAME
    ).read_bytes()
    assert (output / "README.md").read_bytes() == (path.parent / "README.md").read_bytes()


def test_refuses_overwriting_saved_example(real_case):
    path, _, _ = real_case
    before = path.read_bytes()
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        preparation.write_interactive_real_case(path.parent)
    assert path.read_bytes() == before


def test_missing_cache_never_downloads_or_fabricates(tmp_path):
    output = tmp_path / "output"
    with pytest.raises(FileNotFoundError, match="Required existing cached input"):
        preparation.write_interactive_real_case(output, cache_directory=tmp_path / "absent")
    assert not output.exists()
