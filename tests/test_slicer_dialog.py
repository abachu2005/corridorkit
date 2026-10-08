"""Headless tests of the actual adapter using minimal Slicer/Qt test doubles."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setitem(sys.modules, "qt", SimpleNamespace(QDialog=object))
    slicer = SimpleNamespace(util=SimpleNamespace())
    monkeypatch.setitem(sys.modules, "slicer", slicer)
    monkeypatch.setitem(sys.modules, "vtk", SimpleNamespace())
    path = Path(__file__).resolve().parents[1] / "slicer/SkullBaseComparison/CorridorAnalysis.py"
    spec = importlib.util.spec_from_file_location("tested_corridor_dialog", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, slicer


def test_export_preserves_full_grid_and_axis_order(adapter, tmp_path):
    module, slicer = adapter
    array = np.zeros((7, 5, 3), dtype=np.uint8)
    array[6, 4, 2] = 1
    node = SimpleNamespace(GetParentTransformNode=lambda: None)
    volume = SimpleNamespace(
        GetImageData=lambda: SimpleNamespace(GetDimensions=lambda: (3, 5, 7)))
    slicer.util.arrayFromSegmentBinaryLabelmap = lambda n, s, v: array.copy()
    dialog = object.__new__(module.CorridorAnalysisDialog)
    dialog.segments = {"target": SimpleNamespace(
        currentNode=lambda: node, currentSegmentID=lambda: "target-id")}
    path = dialog.array("target", volume, tmp_path)
    actual = np.load(path, allow_pickle=False)
    assert actual.shape == (3, 5, 7)
    assert actual[2, 4, 6] == 1
    assert actual.sum() == 1
    assert actual.flags.c_contiguous
    node.GetParentTransformNode = lambda: object()
    with pytest.raises(ValueError, match="Harden"):
        dialog.array("target", volume, tmp_path)


def test_export_rejects_cropped_reference_grid(adapter, tmp_path):
    module, slicer = adapter
    slicer.util.arrayFromSegmentBinaryLabelmap = lambda *args: np.zeros((2, 3, 4))
    volume = SimpleNamespace(
        GetImageData=lambda: SimpleNamespace(GetDimensions=lambda: (4, 3, 20)))
    dialog = object.__new__(module.CorridorAnalysisDialog)
    dialog.segments = {"target": SimpleNamespace(
        currentNode=lambda: SimpleNamespace(GetParentTransformNode=lambda: None),
        currentSegmentID=lambda: "target")}
    with pytest.raises(ValueError, match="full reference CT grid"):
        dialog.array("target", volume, tmp_path)
    assert not list(tmp_path.iterdir())


def test_optional_missing_segment_has_no_output(adapter, tmp_path):
    module, _ = adapter
    dialog = object.__new__(module.CorridorAnalysisDialog)
    dialog.segments = {"bone": SimpleNamespace(
        currentNode=lambda: None, currentSegmentID=lambda: "")}
    assert dialog.array("bone", None, tmp_path) is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("raw", [b"engine error\n", "engine error\n"])
def test_pythonqt_bytearray_completion_logs_failure(adapter, tmp_path, raw):
    module, _ = adapter
    dialog = object.__new__(module.CorridorAnalysisDialog)
    dialog.run = SimpleNamespace(enabled=False)
    dialog.cancel = SimpleNamespace(enabled=True)
    dialog.status = SimpleNamespace(text="")
    dialog.output = tmp_path
    dialog.process = SimpleNamespace(
        readAllStandardOutput=lambda: SimpleNamespace(data=lambda: raw))
    dialog.finished(2)
    assert dialog.run.enabled and not dialog.cancel.enabled
    assert (tmp_path / "engine.log").read_text() == "engine error\n"
    assert "failed or cancelled" in dialog.status.text
