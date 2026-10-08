"""Real CT open/draw/check/save/reopen/export acceptance; no clinical approval."""
import argparse
import json
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from corridorkit.desktop.app import MainWindow
from corridorkit.export.json import atomic_json_write
from corridorkit.geometry.engine import analyze_case


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    errors = []
    QtWidgets.QMessageBox.critical = lambda *a: errors.append(str(a[-1]))
    window = MainWindow(str(args.case.resolve()))
    window.resize(1660, 1000)
    window.show()
    assert window.viewer.source_volume is not None, errors
    print("Real CT loaded", flush=True)
    center = window.case.target.array().mean(axis=0)
    window.viewer.set_crosshair(center)
    for index, approach in enumerate(window.case.approaches):
        window.intended_approach.setCurrentIndex(index)
        window._begin_intended_path()
        window._point_picked(approach.portal.center_mm)
        window._point_picked(center)
    assert len(window.document.intended_paths) == len(window.case.approaches)
    report = window.check_intended_path()
    assert report["status"] != "clear_of_supplied_geometry"
    result = analyze_case(window.case, base_directory=args.case.parent)
    assert all(a.status.value == "abstained" for a in result.approaches)
    window._analysis_completed(result, window._generation)
    destination = args.output / "saved-plan.json"
    window.case_path = destination
    window.save_case()
    expected = window.document.intended_paths.copy()
    window.open_case(destination)
    assert window.document.intended_paths == expected
    window.viewer.set_crosshair(center)
    window.check_intended_path()
    export = args.output / "exported-plan.json"
    QtWidgets.QFileDialog.getSaveFileName = lambda *a, **kw: (str(export), "JSON")
    window.export_plan()
    payload = json.loads(export.read_text())
    assert payload["intended_path_status"] == "unassessed"
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(1500, loop.quit)
    loop.exec()
    window.grab().save(str(args.output / "real-ct-interactive-plan.png"))
    atomic_json_write(args.output / "report.json", {
        "passed": not errors, "errors": errors, "case_id": window.case.case_id,
        "real_ct_loaded": True, "paths_drawn": len(expected),
        "save_reopen_export_passed": True, "analysis_abstained": True,
        "exact_path_check": report,
        "limits": "Real scan with demonstration region, not tumor or approved surgical entries.",
    })
    print("Real CT draw/check/save/reopen/export passed", flush=True)
    if args.keep_open:
        return app.exec()
    window.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
