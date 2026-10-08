"""Opt-in native bundle verification; not clinician usability acceptance."""
from __future__ import annotations

import json
from pathlib import Path
import platform
import sys
import time

import nibabel as nib
import numpy as np
from PySide6 import QtCore, QtWidgets

from corridorkit.desktop.app import MainWindow
from corridorkit.export.json import atomic_json_write, export_result


def run(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    errors: list[str] = []
    original_critical = QtWidgets.QMessageBox.critical
    original_question = QtWidgets.QMessageBox.question
    QtWidgets.QMessageBox.critical = lambda *a: errors.append(str(a[-1]))
    QtWidgets.QMessageBox.question = (
        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard
    )
    window = None
    checks: dict[str, bool] = {}

    def pump() -> None:
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(25, loop.quit)
        loop.exec()

    def wait() -> None:
        deadline = time.monotonic() + 120
        while window.thread is not None:
            if time.monotonic() > deadline:
                raise TimeoutError("Analysis did not finish within 120 seconds")
            pump()
        pump()

    try:
        window = MainWindow()
        window.show()
        pump()
        window.open_synthetic()
        wait()
        checks["synthetic_analysis"] = (
            window.result is not None
            and len(window.result.approaches) > 0
            and any(a.status.value == "complete" for a in window.result.approaches)
        )
        checks["native_vtk"] = window.viewer.three_d.available
        checks["workspace_screenshot"] = window.grab().save(str(output / "workspace.png"))
        if checks["native_vtk"]:
            import vtk
            render = window.viewer.three_d._vtk_widget.GetRenderWindow()
            render.Render()
            capture = vtk.vtkWindowToImageFilter()
            capture.SetInput(render)
            capture.ReadFrontBufferOff()
            capture.Update()
            writer = vtk.vtkPNGWriter()
            writer.SetFileName(str(output / "native-3d.png"))
            writer.SetInputConnection(capture.GetOutputPort())
            writer.Write()
            checks["native_render"] = (output / "native-3d.png").is_file()
        export_result(output / "analysis.json", window.case, window.result)
        checks["json_export"] = bool(json.loads((output / "analysis.json").read_text()))
        window.case_path = output / "session.json"
        window.save_case()
        saved_id = window.case.case_id
        window.open_case(output / "session.json")
        checks["session_reopen"] = window.case.case_id == saved_id
        image = nib.Nifti1Image(np.zeros((20, 22, 24), np.int16), np.diag([1, 2, 3, 1]))
        image.header.set_xyzt_units("mm")
        image_path = output / "synthetic-ct.nii.gz"
        nib.save(image, image_path)
        window.import_ct(image_path)
        checks["ct_import"] = window.viewer.volume is not None
        window.start_analysis()
        wait()
        checks["unreviewed_ct_abstains"] = (
            window.result is not None
            and all(a.status.value == "abstained" for a in window.result.approaches)
        )
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        if window is not None:
            if window.thread is not None:
                window.cancel_analysis()
                wait()
            window.close()
            pump()
        QtWidgets.QMessageBox.critical = original_critical
        QtWidgets.QMessageBox.question = original_question
    report = {
        "passed": bool(checks) and all(checks.values()) and not errors,
        "checks": checks,
        "errors": errors,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
        "platform": platform.platform(),
        "limitation": "Automated native smoke on this Mac; not human acceptance or notarization.",
    }
    atomic_json_write(output / "report.json", report)
    print(json.dumps(report), flush=True)
    return 0 if report["passed"] else 1
