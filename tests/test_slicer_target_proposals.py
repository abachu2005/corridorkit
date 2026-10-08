"""Headless contracts for the Slicer entry-proposal bridge."""
import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "slicer/SkullBaseComparison/EntryProposals.py"
MODULE = ROOT / "slicer/SkullBaseComparison/SkullBaseComparison.py"
VERIFY = ROOT / "slicer/verify_target_proposals_in_slicer.py"


def load_helper(monkeypatch):
    monkeypatch.setitem(sys.modules, "qt", SimpleNamespace(QDialog=object))
    monkeypatch.setitem(sys.modules, "slicer", SimpleNamespace(util=SimpleNamespace()))
    monkeypatch.setitem(sys.modules, "vtk", SimpleNamespace())
    spec = importlib.util.spec_from_file_location("tested_entry_proposals", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_slicer_proposal_files_compile_and_helper_is_hidden():
    for path in (HELPER, MODULE, VERIFY):
        ast.parse(path.read_text(), filename=str(path))
    tree = ast.parse(HELPER.read_text())
    helper_class = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == "EntryProposals")
    source = ast.get_source_segment(HELPER.read_text(), helper_class)
    assert "parent.hidden = True" in source


def test_bridge_contract_calls_propose_entries_with_xyz_arrays(monkeypatch, tmp_path):
    helper = load_helper(monkeypatch)
    mask = np.zeros((5, 7, 9), dtype=np.uint8)
    mask[2, 3, 4] = 1
    mask_path = tmp_path / "left_nasal_cavity.npy"
    np.save(mask_path, mask, allow_pickle=False)
    request = {
        "schema_version": 1,
        "operation": "propose_entries",
        "coordinate_frame": "RAS_mm",
        "array_axis_order": "XYZ_IJK",
        "target_ras_mm": [1.0, 2.0, 3.0],
        "affine": np.eye(4).tolist(),
        "masks": {"left_nasal_cavity": {"path": str(mask_path), "status": "reviewed"}},
        "parameters": {"candidates_per_surface": 2},
    }
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request))
    captured = {}

    def propose_entries(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(model_dump=lambda mode: {
            "status": "abstained", "candidates": [], "missing_constraints": ["synthetic"]
        })

    fake = SimpleNamespace(propose_entries=propose_entries)
    real_import = helper.importlib.import_module
    monkeypatch.setattr(
        helper.importlib, "import_module",
        lambda name: fake if name.endswith(".entry_proposals") else real_import(name))
    output = tmp_path / "output.json"
    result = helper.run_bridge(request_path, output)
    assert result["status"] == "abstained"
    assert captured["target_ras_mm"] == [1.0, 2.0, 3.0]
    assert captured["masks"]["left_nasal_cavity"].shape == (5, 7, 9)
    assert captured["masks"]["left_nasal_cavity"][2, 3, 4] == 1
    assert captured["affine"].shape == (4, 4)
    assert captured["candidates_per_surface"] == 2
    assert json.loads(output.read_text()) == result


def test_export_preserves_full_ct_grid_xyz(monkeypatch, tmp_path):
    helper = load_helper(monkeypatch)
    slicer = sys.modules["slicer"]
    kji = np.zeros((9, 7, 5), dtype=np.uint8)
    kji[8, 6, 4] = 1
    slicer.util.arrayFromSegmentBinaryLabelmap = lambda *args: kji.copy()
    selector = SimpleNamespace(
        currentNode=lambda: SimpleNamespace(GetParentTransformNode=lambda: None),
        currentSegmentID=lambda: "segment",
    )
    dialog = object.__new__(helper.EntryProposalDialog)
    dialog.segments = {"bone": selector}
    volume = SimpleNamespace(
        GetImageData=lambda: SimpleNamespace(GetDimensions=lambda: (5, 7, 9)))
    path = dialog.exportMask("bone", volume, tmp_path)
    exported = np.load(path, allow_pickle=False)
    assert exported.shape == (5, 7, 9)
    assert exported[4, 6, 8] == 1
    assert exported.flags.c_contiguous


def test_ui_contract_uses_only_nonapproval_states():
    source = MODULE.read_text() + HELPER.read_text()
    assert '"proposed"' in source
    assert '"conditional"' in source
    assert '"unavailable"' in source
    assert '"approved"' not in source
    assert "Portal" in source
    assert "SetNodeReferenceID" in source
