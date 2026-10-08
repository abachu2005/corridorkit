"""Exercise the native desktop with a real source volume and save evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from PySide6 import QtWidgets, QtCore

from skullbase_corridor.desktop.app import MainWindow
from skullbase_corridor.export.json import atomic_json_write, export_result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path)
    parser.add_argument("--case", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    errors = []
    QtWidgets.QMessageBox.critical = lambda *a: errors.append(str(a[-1]))
    QtWidgets.QMessageBox.question = lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard
    window = MainWindow()
    print("constructed", flush=True)
    window.show()
    print("shown", flush=True)
    def pump():
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(20, loop.quit)
        loop.exec()
    pump()
    print("event-loop-ready", flush=True)
    if args.case:
        window.open_case(args.case)
        if args.labels:
            from skullbase_corridor.io.volumes import load_volume
            window.viewer.set_mask("Supporting labels", load_volume(args.labels))
        window.viewer.reset_view()
        window.start_analysis()
    elif args.image:
        window.import_ct(args.image)
        if args.labels:
            from skullbase_corridor.io.volumes import load_volume
            window.viewer.set_mask("Public supporting labels — not critical anatomy",
                                   load_volume(args.labels))
        pump()
        window.viewer.reset_view()
        window.start_analysis()
    else:
        window.open_synthetic()
    print("analysis-started", flush=True)
    start = time.perf_counter()
    while window.thread is not None and time.perf_counter() - start < 120:
        pump()
    elapsed = time.perf_counter() - start
    print("analysis-finished", flush=True)
    if window.thread:
        window.cancel_analysis()
        while window.thread:
            pump()
        raise RuntimeError("Desktop smoke exceeded 120 seconds")
    pump()
    native_vtk = window.viewer.three_d.available
    window.grab().save(str(args.output / "workspace.png"))
    if native_vtk:
        import vtk
        render = window.viewer.three_d._vtk_widget.GetRenderWindow()
        render.Render()
        image = vtk.vtkWindowToImageFilter()
        image.SetInput(render)
        image.ReadFrontBufferOff()
        image.Update()
        writer = vtk.vtkPNGWriter()
        writer.SetFileName(str(args.output / "native-3d.png"))
        writer.SetInputConnection(image.GetOutputPort())
        writer.Write()
    if window.result is not None:
        export_result(args.output / "analysis.json", window.case, window.result)
    record = {
        "source": "public_computational_case" if args.case else "public_image" if args.image else "synthetic",
        "analysis_seconds": elapsed,
        "native_vtk": native_vtk,
        "result_statuses": [a.status.value for a in window.result.approaches] if window.result else [],
        "errors": errors,
        "limitations": "Programmatic smoke and screenshot; not independent human usability acceptance.",
    }
    atomic_json_write(args.output / "smoke.json", record)
    window.close()
    pump()
    print(json.dumps(record))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
