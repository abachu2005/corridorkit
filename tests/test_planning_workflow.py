"""Integrated path selection, comparison and stale-result desktop contracts."""
import importlib.util
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.skipif(importlib.util.find_spec("PySide6") is None,
                               reason="optional desktop")


def test_planning_path_selection_and_stale_invalidation(monkeypatch):
    from PySide6 import QtCore, QtWidgets

    from skullbase_corridor.desktop.app import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                        lambda *a: pytest.fail(f"Unexpected GUI error: {a[-1]}"))
    window = MainWindow()
    window.open_synthetic()
    timer = QtCore.QElapsedTimer()
    timer.start()
    while window.thread is not None and timer.elapsed() < 15000:
        app.processEvents()
    assert window.thread is None
    assert window.result is not None
    assert window.path_selector.count() > 0
    assert window.path_controls.isEnabled()
    assert window.viewer.coverage_labels is not None
    assert len(window.viewer.coverage_labels) == len(window.case.target.points_mm)
    assert window.viewer.detail_tabs.currentWidget() is window.viewer.path_view
    assert window.review_panel.isHidden()
    assert window.viewer.planning_splitter.widget(0) is window.viewer.views[0]
    assert "TM adds" in window.coverage_strip.text()
    for row, approach in enumerate(window.result.approaches):
        window._show_witness(row)
        assert window.path_selector.currentData()[0] == approach.name
        _, index = window.path_selector.currentData()
        assert approach.trajectories[index].feasible
        assert not approach.trajectories[index].conditional
    original_count = window.path_selector.count()
    target_index = window.result.approaches[0].reached_point_indices[0]
    window._point_picked(window.case.target.points_mm[target_index])
    assert window.path_selector.count() > 0
    for i in range(window.path_selector.count()):
        name, index = window.path_selector.itemData(i)
        approach = next(a for a in window.result.approaches if a.name == name)
        assert target_index in approach.trajectories[index].reached_point_indices
    window._clear_target_filter()
    assert window.path_selector.count() == original_count
    for mode, kind in ((1, "eea"), (2, "transmaxillary")):
        window.viewer.approach_display.setCurrentIndex(mode)
        assert window.path_selector.count() > 0
        for index in range(window.path_selector.count()):
            name, _ = window.path_selector.itemData(index)
            assert next(a for a in window.case.approaches if a.name == name).kind.value == kind
        assert window.viewer.selected_trajectory.approach_name in window.viewer.visible
    window.viewer.approach_display.setCurrentIndex(0)
    assert window.path_selector.count() == original_count
    window._mark_stale()
    assert window.path_selector.count() == 0
    assert not window.path_controls.isEnabled()
    assert window.viewer.coverage_labels is None
    assert window.viewer.selected_trajectory is None
    assert not window.export_action.isEnabled()
    window.close()


def test_planning_export_includes_point_categories(tmp_path, monkeypatch):
    import json

    from PySide6 import QtWidgets

    from skullbase_corridor.desktop.app import MainWindow
    from skullbase_corridor.geometry.engine import analyze_case
    from skullbase_corridor.synthetic.cases import analytical_case
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    window.case = analytical_case()
    window.result = analyze_case(window.case)
    destination = tmp_path / "analysis.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(destination), "JSON"))
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                        lambda *a: pytest.fail(str(a[-1])))
    window.choose_export()
    exported = json.loads((tmp_path / "analysis-coverage/comparison.json").read_text())
    assert len(exported["categories"]) == len(window.case.target.points_mm)
    assert exported["volume_mm3"] is None
    assert (tmp_path / "analysis-coverage/comparison_points.csv").is_file()
    window.close()


def test_conditional_paths_are_not_selectable(monkeypatch):
    from PySide6 import QtWidgets

    from skullbase_corridor.desktop.app import MainWindow
    from skullbase_corridor.geometry.engine import analyze_case
    from skullbase_corridor.synthetic.cases import analytical_case
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    # Obtain the same fixture through the public factory, without UI threads.
    case = analytical_case()
    result = analyze_case(case)
    window.case = case
    window.result = result.model_copy(update={"approaches": [
        approach.model_copy(update={"trajectories": [
            path.model_copy(update={"conditional": True})
            for path in approach.trajectories
        ]}) for approach in result.approaches
    ]})
    window.viewer.set_case(case)
    window.viewer.set_result(window.result)
    window._populate_paths()
    assert window.path_selector.count() == 0
    assert not window.path_controls.isEnabled()
    window.close()
    app.processEvents()


def test_target_import_volume_is_explicit(monkeypatch):
    from PySide6 import QtWidgets

    from skullbase_corridor.desktop.app import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    assert window._choose_target_stride(20) == 1
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem",
                        lambda *a: ("Full voxel target", True))
    assert window._choose_target_stride(20001) == 1
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem",
                        lambda *a: ("Sparse preview", True))
    assert window._choose_target_stride(20001) == 11
    monkeypatch.setattr(QtWidgets.QInputDialog, "getItem",
                        lambda *a: ("", False))
    assert window._choose_target_stride(20001) is None
    window.close()
    app.processEvents()
