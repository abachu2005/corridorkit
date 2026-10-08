import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_extension_metadata_and_integrated_controller_are_packaged():
    assert (ROOT / "CMakeLists.txt").is_file()
    descriptor = (ROOT / "CorridorKit.s4ext").read_text()
    assert "category IGT" in descriptor
    assert (ROOT / "slicer/SkullBaseComparison/Resources/Icons/SkullBaseComparison.png").is_file()
    module = (ROOT / "slicer/SkullBaseComparison/CMakeLists.txt").read_text()
    assert "IntegratedPlanning.py" in module
    assert "Resources/Icons/SkullBaseComparison.png" in module
    ast.parse((ROOT / "slicer/SkullBaseComparison/IntegratedPlanning.py").read_text())


def test_runtime_builder_excludes_redundant_desktop_stack():
    builder = (ROOT / "packaging/build_slicer_runtime.py").read_text()
    assert "excludes_desktop_qt_vtk" in builder
    assert '".[desktop]"' not in builder


def test_primary_slicer_ui_exposes_one_click_product_action():
    source = (ROOT / "slicer/SkullBaseComparison/SkullBaseComparison.py").read_text()
    assert 'QPushButton("Plan Corridors")' in source
    assert "setCandidates(" in source
    assert "exact_state" in source
    assert "IntegratedResult" in (
        source + (ROOT / "slicer/SkullBaseComparison/IntegratedPlanning.py").read_text()
    )
