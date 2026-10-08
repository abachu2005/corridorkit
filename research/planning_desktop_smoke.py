"""Exercise the scan-centered planning workflow on an existing public CT case."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from PySide6 import QtCore, QtWidgets

from skullbase_corridor.desktop.app import MainWindow
from skullbase_corridor.export.json import atomic_json_write


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
    QtWidgets.QMessageBox.question = lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Discard
    window = MainWindow(str(args.case.resolve()))
    window.resize(1660, 1000)
    window.show()
    print("Case loaded; native window shown", flush=True)

    def pump():
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(40, loop.quit)
        loop.exec()

    pump()
    window.start_analysis()
    deadline = time.monotonic() + 180
    while window.thread is not None and time.monotonic() < deadline:
        pump()
    if window.thread is not None:
        window.cancel_analysis()
        while window.thread is not None:
            pump()
        raise RuntimeError("Planning smoke timed out")
    assert window.result is not None, errors
    print("Analysis complete", flush=True)
    assert window.viewer.source_volume is not None, "CT did not load"
    assert window.path_selector.count() > 0, "No selectable feasible path"
    window._focus_path()
    pump()
    window.grab().save(str(args.output / "planning-workspace.png"))
    window.viewer.path_view.grab().save(str(args.output / "path-aligned-ct.png"))
    window.comparison_panel.grab().save(str(args.output / "coverage-comparison.png"))
    selected = []
    for kind in ("eea", "transmaxillary"):
        names = {a.name for a in window.case.approaches if a.kind.value == kind}
        for index in range(window.path_selector.count()):
            name, trajectory_index = window.path_selector.itemData(index)
            if name in names:
                window.path_selector.setCurrentIndex(index)
                window._focus_path()
                pump()
                window.grab().save(str(args.output / f"{kind}-path.png"))
                window.viewer.path_view.grab().save(str(args.output / f"{kind}-oblique.png"))
                selected.append({"kind": kind, "approach": name, "trajectory_index": trajectory_index})
                break
    assert len(selected) == 2, "Both approaches need a demonstrated path in this fixture"
    before = window.path_selector.count()
    full_target = getattr(window.case.target, "source", "") == "mask_voxel_centers"
    if full_target:
        center = window.case.target.array().mean(axis=0)
        window.viewer.set_crosshair(center)
        pump()
        kinds = [getattr(item, "planning_kind", "") for item in window.viewer.views[0].overlays]
        assert "target_slice" in kinds
        assert "target_slice_outline" in kinds
        assert "portal_projection" in kinds
        assert "instrument_projection" in kinds
        window.grab().save(str(args.output / "segmented-target-overview.png"))
    for mode in (1, 2, 0):
        window.viewer.approach_display.setCurrentIndex(mode)
        pump()
        assert window.path_selector.count() > 0
        window.grab().save(str(args.output / f"approach-mode-{mode}.png"))
    window.viewer.corridor_toggle.setChecked(True)
    pump()
    window.grab().save(str(args.output / "sampled-occupancy.png"))
    window.viewer.corridor_toggle.setChecked(False)
    window.viewer.detail_tabs.setCurrentWidget(window.viewer.three_d)
    pump()
    window.grab().save(str(args.output / "instrument-3d.png"))
    window.viewer.detail_tabs.setCurrentWidget(window.viewer.path_view)
    window._mark_stale()
    assert window.path_selector.count() == 0
    assert not window.path_controls.isEnabled()
    record = {
        "case_id": window.case.case_id,
        "source_ct_loaded": True,
        "native_vtk": window.viewer.three_d.available,
        "selectable_paths": before,
        "selected_paths": selected,
        "stale_paths_cleared": True,
        "full_target_contours_verified": full_target,
        "approach_modes_verified": True,
        "errors": errors,
        "passed": not errors,
        "limitations": "Synthetic or public computational targets/portals, not validated operative anatomy. "
                       "Programmatic desktop verification, not clinician acceptance.",
    }
    atomic_json_write(args.output / "report.json", record)
    print(json.dumps(record), flush=True)
    if args.keep_open:
        window.start_analysis()
        return app.exec()
    window.close()
    pump()
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
