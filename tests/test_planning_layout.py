"""The scan must not become a canvas for overlapping explanatory text."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("CORRIDORKIT_DISABLE_VTK", "1")

import numpy as np
import pyqtgraph as pg
from PySide6 import QtWidgets

from corridorkit.desktop.app import MainWindow
from corridorkit.io.volumes import Volume
from corridorkit.synthetic.cases import analytical_case


def test_inspector_controls_fit_and_scan_annotations_stay_outside_image():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = MainWindow()
    case = analytical_case()
    window._set_case(case, from_history=True)
    window.viewer.set_volume(Volume(np.zeros((32, 32, 32)), np.eye(4), "CT"))
    approach = case.approaches[0]
    entry = np.asarray(approach.portal.center_mm)
    tip = entry + 10 * np.asarray(approach.nominal_direction)
    window.add_intended_path(approach.name, entry, tip)
    window.show()
    for width, height in ((1660, 1000), (1280, 800), (1024, 768)):
        window.resize(width, height)
        app.processEvents()
        assert window.width() <= width
        assert window.viewer.views[0].width() >= 250
        assert window.intended_approach.width() <= 340
        assert window.intended_approach.currentData() == approach.name
        for view in window.viewer.views:
            assert not any(isinstance(item, pg.TextItem) and item.isVisible()
                           for item in view.overlays)
        controls = window.intended_approach.parentWidget().findChildren(QtWidgets.QPushButton)
        visible = [control for control in controls if control.isVisible()]
        for i, left in enumerate(visible):
            for right in visible[i + 1:]:
                if left.parentWidget() is right.parentWidget():
                    assert not left.geometry().intersects(right.geometry())
    window._dirty = False
    window.close()
