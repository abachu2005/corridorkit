"""Checkout packaging contracts, not substitutes for a native Slicer test."""
import ast
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "slicer/SkullBaseComparison/SkullBaseComparison.py"


def test_slicer_adapter_and_native_smoke_compile():
    for path in (MODULE, ROOT / "slicer/verify_in_slicer.py",
                 ROOT / "slicer/load_anatomical_case.py",
                 ROOT / "slicer/SkullBaseComparison/CorridorAnalysis.py"):
        ast.parse(path.read_text(), filename=str(path))


def test_atlas_loader_uses_downloader_cache_without_loading_on_import(monkeypatch):
    monkeypatch.setitem(sys.modules, "slicer", SimpleNamespace())
    spec = importlib.util.spec_from_file_location(
        "atlas_loader_contract", ROOT / "slicer/load_anatomical_case.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DEFAULT_ATLAS_DIRECTORY == ROOT / "data-cache/skullbase-atlas"


def test_measurement_core_loads_without_slicer_or_application_dependencies():
    spec = importlib.util.spec_from_file_location(
        "isolated_anatomical_core", ROOT / "src/corridorkit/analysis/anatomical.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.compare_landmarks(
        (0, 10, 0), (10, 0, 0), (0, 0, 0), (0, 0, 0), (10, 0, 0))
    assert result["angle_advantage_deg"] == 90
    assert result["additional_lateral_reach_mm"] is None
