import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from corridorkit.analysis.intended import assess_intended_path
from corridorkit.application.session import IntendedPath, ReviewDocument
from corridorkit.domain.models import ProtectedStructure, SphereGeometry
from corridorkit.synthetic.cases import analytical_case


def make_path(case):
    approach = case.approaches[0]
    entry = np.array(approach.portal.center_mm)
    tip = entry + np.array(approach.nominal_direction) * 10
    return IntendedPath(approach_name=approach.name, entry_mm=tuple(entry), tip_mm=tuple(tip))


def test_missing_critical_anatomy_cannot_be_clear():
    case = analytical_case()
    case = case.model_copy(update={"protected_structures": [
        ProtectedStructure(name="carotids", status="unknown"),
        ProtectedStructure(name="cranial nerves", status="unknown"),
    ]})
    report = assess_intended_path(case, make_path(case))
    assert report["status"] == "unassessed"
    assert report["unassessed_structures"] == ["carotids", "cranial nerves"]
    with pytest.raises(ValueError):
        IntendedPath(approach_name="a", entry_mm=(0, 0, 0), tip_mm=(0, 0, 0))


def test_plan_persists_without_becoming_analysis(tmp_path):
    case = analytical_case()
    doc = ReviewDocument(case=case, intended_paths=[make_path(case)])
    path = tmp_path / "plan.json"
    doc.save(path)
    loaded = ReviewDocument.model_validate_json(path.read_text())
    assert loaded.intended_paths == doc.intended_paths
    assert not loaded.anatomy_approved


def test_exact_path_collision_and_off_portal_entry():
    case = analytical_case()
    path = make_path(case)
    midpoint = (np.array(path.entry_mm) + path.tip_mm) / 2
    obstacle = ProtectedStructure(name="protected", status="known",
                                  geometry=SphereGeometry(center_mm=tuple(midpoint), radius_mm=2))
    case = case.model_copy(update={"protected_structures": [obstacle]})
    report = assess_intended_path(case, path)
    assert report["status"] == "blocked"
    assert report["clearances_mm"]["protected"] < 0
    away = obstacle.model_copy(update={"geometry": SphereGeometry(
        center_mm=tuple(midpoint + 100), radius_mm=2)})
    case = case.model_copy(update={"protected_structures": [away]})
    assert assess_intended_path(case, path)["status"] == "clear_of_supplied_geometry"
    shifted = path.model_copy(update={"entry_mm": tuple(np.array(path.entry_mm) + 100)})
    assert assess_intended_path(case, shifted)["status"] == "blocked"


def test_forged_path_and_coordinate_mismatch_rejected():
    case = analytical_case()
    path = make_path(case)
    with pytest.raises(ValueError, match="distinct"):
        assess_intended_path(case, path.model_copy(update={"tip_mm": path.entry_mm}))
    with pytest.raises(ValueError, match="RAS"):
        assess_intended_path(case.model_copy(update={"coordinate_frame": "LPS"}), path)


def test_draw_save_reopen_export_with_abstention(tmp_path, monkeypatch):
    from PySide6 import QtWidgets

    from corridorkit.desktop.app import MainWindow
    from corridorkit.export.json import write_case
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    case = analytical_case().model_copy(update={"protected_structures": [
        ProtectedStructure(name="carotid", status="unknown"),
    ]})
    source = tmp_path / "case.json"
    write_case(source, case)
    window = MainWindow(str(source))
    path = make_path(case)
    window.add_intended_path(path.approach_name, path.entry_mm, path.tip_mm)
    assert window.check_intended_path()["status"] == "unassessed"
    window.save_case()
    window.open_case(source)
    assert window.document.intended_paths == [path]
    assert window.viewer.intended_paths == [path]
    destination = tmp_path / "export.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        lambda *a, **kw: (str(destination), "JSON"))
    window.export_plan()
    assert '"intended_path_status": "unassessed"' in destination.read_text()
    assert '"supplied_geometry_checks"' in destination.read_text()
    window.close()
    app.processEvents()
