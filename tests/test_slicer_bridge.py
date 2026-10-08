"""Actual engine integration for full-grid Slicer exports (no Slicer required)."""

import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from skullbase_corridor.application.slicer_bridge import (
    BONE_NAME,
    UNKNOWN_NAME,
    SlicerBridgeRequest,
    build_case,
    run_request,
)
from skullbase_corridor.geometry.engine import analyze_case


@pytest.fixture
def exported(tmp_path):
    shape = (25, 25, 25)
    target = np.zeros(shape, dtype=np.uint8)
    target[12, 12, 18] = 1
    bone = np.zeros(shape, dtype=np.uint8)
    bone[:, :, 10] = 1
    protected = np.zeros(shape, dtype=np.uint8)
    protected[2, 2, 2] = 1
    for name, array in (("target", target), ("bone", bone), ("protected", protected),
                        ("removal", bone)):
        np.save(tmp_path / f"{name}.npy", array)
    return {
        "target": "target.npy",
        "bone": "bone.npy",
        "protected": [{"name": "reviewed-critical", "path": "protected.npy"}],
        "affine": np.eye(4).tolist(),
        "entries": {"EEA": [12, 12, 3], "CTM": [16, 12, 3]},
        "target_point": [12, 12, 18],
        "shaft_diameter_mm": 1.0,
        "portal_diameter_mm": 5.0,
        "instrument_length_mm": 30,
        "anatomy_complete": True,
        "removals_reviewed": False,
        "reviewer": "Synthetic test reviewer",
        "sampling": {"polar_steps": 1, "azimuth_steps": 4, "max_target_witnesses": 4},
    }


def run(exported, tmp_path, directory="output"):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(exported))
    return run_request(path, tmp_path / directory)


def test_obstruction_then_reviewed_bone_removal_restores_coverage(exported, tmp_path):
    blocked = run(exported, tmp_path, "blocked")
    assert blocked["coverage"]["counts"]["not_reached"] == 1
    assert all(a["status"] == "no_feasible_trajectory" for a in blocked["result"]["approaches"])
    exported.update(removals={"CTM": "removal.npy"}, removals_reviewed=True)
    opened = run(exported, tmp_path, "opened")
    assert opened["coverage"]["counts"]["tm_only"] == 1
    assert opened["coverage"]["volume_mm3"]["tm_only"] == 1
    assert opened["result"]["approaches"][0]["reached_point_indices"] == []
    assert opened["result"]["approaches"][1]["reached_point_indices"] == [0]
    assert opened["review_declaration"]["automated_anatomy_approval"] is False
    assert "user declarations only" in opened["review_declaration"]["note"]
    assert json.loads((tmp_path / "opened/result.json").read_text()) == opened
    for filename in opened["files"].values():
        assert (tmp_path / "opened" / filename).is_file()
    case = build_case(exported, base_directory=tmp_path)
    assert case.target.point_volume_mm3 == 1
    assert case.target.source == "mask_voxel_centers"
    assert len(case.target.points_mm) == 1
    for approach in case.approaches:
        np.testing.assert_allclose(approach.portal.normal, approach.nominal_direction)
        assert approach.target_tolerance_mm == 0
        assert not approach.allow_unknown_anatomy
    assert np.count_nonzero(np.load(tmp_path / "bone.npy")) == 25 * 25


def test_virtual_removal_never_erases_overlapping_protected_tissue(exported, tmp_path):
    np.save(tmp_path / "protected.npy", np.load(tmp_path / "bone.npy"))
    exported.update(
        removals={"EEA": "removal.npy", "CTM": "removal.npy"}, removals_reviewed=True,
    )
    result = run(exported, tmp_path)
    assert result["coverage"]["counts"]["not_reached"] == 1
    case = build_case(exported, base_directory=tmp_path)
    critical = next(s for s in case.protected_structures if s.name == "reviewed-critical")
    assert critical.tissue_type == "critical"
    assert not critical.allow_virtual_removal
    assert all(r.structure_name == BONE_NAME for a in case.approaches
               for r in a.virtual_bone_removals)


@pytest.mark.parametrize("changes", [
    {"anatomy_complete": False}, {"reviewer": ""}, {"reviewer": "   "},
    {"protected": []}, {"anatomy_complete": False, "reviewer": "", "protected": []},
])
def test_incomplete_declaration_abstains(exported, tmp_path, changes):
    exported.update(changes)
    case = build_case(exported, base_directory=tmp_path)
    assert any(s.name == UNKNOWN_NAME for s in case.protected_structures)
    result = run(exported, tmp_path)
    assert all(a["status"] == "abstained" for a in result["result"]["approaches"])
    assert result["coverage"]["categories"] == ["unavailable"]
    assert result["coverage"]["eea_reached"] == [None]


@pytest.mark.parametrize("role", ["target", "bone", "protected", "removal"])
@pytest.mark.parametrize("bad_value", [2, -1, float("nan"), float("inf")])
def test_every_mask_must_be_binary(exported, tmp_path, role, bad_value):
    array = np.load(tmp_path / f"{role}.npy").astype(float)
    array[0, 0, 0] = bad_value
    np.save(tmp_path / f"{role}.npy", array)
    exported.update(removals={"EEA": "removal.npy"}, removals_reviewed=True)
    with pytest.raises(ValueError, match="binary"):
        build_case(exported, base_directory=tmp_path)


@pytest.mark.parametrize("role", ["bone", "protected", "removal"])
def test_masks_must_share_full_grid(exported, tmp_path, role):
    np.save(tmp_path / f"{role}.npy", np.ones((24, 25, 25), dtype=np.uint8))
    exported.update(removals={"EEA": "removal.npy"}, removals_reviewed=True)
    with pytest.raises(ValueError, match="full CT grid"):
        build_case(exported, base_directory=tmp_path)


@pytest.mark.parametrize("affine", [
    np.zeros((4, 4)).tolist(),
    [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 1, 1]],
    [[1, 0, 0, float("nan")], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
])
def test_invalid_affine_rejected(exported, tmp_path, affine):
    exported["affine"] = affine
    with pytest.raises(ValueError):
        build_case(exported, base_directory=tmp_path)


@pytest.mark.parametrize("changes", [
    {"coordinate_frame": "LPS"}, {"array_order": "zyx"}, {"units": "m"},
    {"transform": np.eye(4).tolist()}, {"anatomy_complete": "true"},
    {"entries": {"EEA": [], "CTM": []}},
    {"entries": {"EEA": [1, 2]}},
    {"entries": {"EEA": [12, 12, 18]}},
    {"sampling": {"polar_steps": 100000}},
    {"max_target_count": 2001},
])
def test_invalid_contract_rejected(exported, changes):
    exported.update(changes)
    with pytest.raises(ValueError):
        SlicerBridgeRequest.model_validate(exported)


def test_mask_specific_affine_rejected(exported):
    exported["protected"][0]["affine"] = np.eye(4).tolist()
    with pytest.raises(ValueError, match="Extra inputs"):
        SlicerBridgeRequest.model_validate(exported)


@pytest.mark.parametrize("changes", [
    {"removals_reviewed": False}, {"reviewer": ""}, {"bone": None},
    {"entries": {"EEA": [12, 12, 3], "CTM": []}},
])
def test_removal_requires_bone_review_and_entry(exported, tmp_path, changes):
    exported.update(removals={"CTM": "removal.npy"}, removals_reviewed=True)
    exported.update(changes)
    with pytest.raises(ValueError):
        build_case(exported, base_directory=tmp_path)


def test_removal_must_be_subset_of_bone(exported, tmp_path):
    mask = np.load(tmp_path / "removal.npy")
    mask[0, 0, 0] = 1
    np.save(tmp_path / "removal.npy", mask)
    exported.update(removals={"EEA": "removal.npy"}, removals_reviewed=True)
    with pytest.raises(ValueError, match="subset"):
        build_case(exported, base_directory=tmp_path)


def test_target_cap_rejects_without_silent_subsampling(exported, tmp_path):
    np.save(tmp_path / "target.npy", np.ones((25, 25, 25), dtype=np.uint8))
    with pytest.raises(ValueError, match="Do not crop"):
        build_case(exported, base_directory=tmp_path)


def test_empty_protected_mask_is_not_completeness_evidence(exported, tmp_path):
    np.save(tmp_path / "protected.npy", np.zeros((25, 25, 25), dtype=np.uint8))
    with pytest.raises(ValueError, match="empty masks"):
        build_case(exported, base_directory=tmp_path)


def test_world_ras_affine_preserved_and_optional_bone_single_entry(exported, tmp_path):
    exported["affine"] = [
        [0, -2, 0, 100], [1, 0, 0, -50], [0, 0, 3, 20], [0, 0, 0, 1],
    ]
    exported["target_point"] = [76, -38, 74]
    exported["entries"] = {"EEA": [76, -38, 29], "CTM": []}
    exported["instrument_length_mm"] = 60
    exported["bone"] = None
    case = build_case(exported, base_directory=tmp_path)
    assert case.target.points_mm == [(76, -38, 74)]
    assert case.target.point_volume_mm3 == pytest.approx(6)
    assert len(case.approaches) == 1
    result = analyze_case(case)
    assert result.approaches[0].reached_point_indices == [0]


def test_cli_round_trip_and_invalid_request_exit_code(exported, tmp_path):
    request = tmp_path / "request.json"
    request.write_text(json.dumps(exported))
    root = Path(__file__).resolve().parents[1]
    command = [
        sys.executable, str(root / "research/run_slicer_bridge.py"),
        "--request", str(request), "--output-dir", str(tmp_path / "cli"),
    ]
    completed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert Path(json.loads(completed.stdout)["result_path"]).is_file()
    request.write_text(json.dumps({**exported, "units": "metres"}))
    failed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert failed.returncode == 2
    assert "error" in json.loads(failed.stderr)


def test_changed_input_rejected_before_export(exported, tmp_path, monkeypatch):
    from skullbase_corridor.application import slicer_bridge

    actual_analyze = slicer_bridge.analyze_case

    def mutate(case, **kwargs):
        result = actual_analyze(case, **kwargs)
        target = np.load(tmp_path / "target.npy")
        target[12, 12, 17] = 1
        np.save(tmp_path / "target.npy", target)
        return result

    monkeypatch.setattr(slicer_bridge, "analyze_case", mutate)
    with pytest.raises(ValueError, match="changed during analysis"):
        run(exported, tmp_path)
    assert not (tmp_path / "output/result.json").exists()
