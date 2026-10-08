from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import numpy as np
import pytest
import SimpleITK as sitk

from corridorkit.domain.models import CorridorCase, ImageReference
from corridorkit.synthetic.cases import analytical_case

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None or importlib.util.find_spec("pyqtgraph") is None,
    reason="desktop dependencies are optional",
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6 import QtWidgets

    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def wait_for_analysis(app, window, timeout_ms: int = 5000) -> None:
    from PySide6 import QtCore

    timer = QtCore.QElapsedTimer()
    timer.start()
    while window.thread is not None and timer.elapsed() < timeout_ms:
        app.processEvents()
    assert window.thread is None


def test_landing_and_synthetic_analysis(app):
    from corridorkit.desktop.app import MainWindow

    window = MainWindow()
    assert window.stack.currentIndex() == 0
    window.open_synthetic()
    wait_for_analysis(app, window)
    assert window.stack.currentIndex() == 1
    assert window.result is not None
    assert window.results.rowCount() == 2
    assert "union" in window.combination_text.text()
    window.close()


def test_edit_stale_undo_save_reopen_export(app, tmp_path: Path):
    from PySide6 import QtWidgets

    from corridorkit.desktop.app import MainWindow
    from corridorkit.export.json import export_result, read_case, write_case

    window = MainWindow()
    window.open_synthetic()
    wait_for_analysis(app, window)
    original = window.case.approaches[0].instrument.length_mm
    spins = window.approach_box.findChildren(QtWidgets.QDoubleSpinBox)
    length = next(
        spin
        for spin in spins
        if spin.property("approach_index") == 0
        and spin.property("field_path") == "instrument.length_mm"
    )
    length.setValue(original + 5)
    length.editingFinished.emit()
    app.processEvents()
    assert window.result is None
    assert window.case.approaches[0].instrument.length_mm == original + 5
    window.undo_stack.undo()
    assert window.case.approaches[0].instrument.length_mm == original

    case_path = tmp_path / "case.json"
    write_case(case_path, window.case)
    assert read_case(case_path) == window.case
    window.open_case(case_path)
    window.start_analysis()
    wait_for_analysis(app, window)
    output = tmp_path / "analysis.json"
    envelope = export_result(output, window.case, window.result)
    assert output.is_file()
    assert envelope["case_id"] == window.case.case_id
    window.close()


def test_attached_volume_populates_linked_slice_controls(app, tmp_path: Path):
    from corridorkit.desktop.app import MainWindow
    from corridorkit.export.json import write_case

    image_path = tmp_path / "ct.nrrd"
    image = sitk.GetImageFromArray(np.arange(9 * 10 * 11).reshape(9, 10, 11).astype("int16"))
    image.SetSpacing((0.7, 0.8, 1.5))
    sitk.WriteImage(image, str(image_path))
    # NRRD writers omit units by default; clinical geometry must declare them.
    payload = image_path.read_bytes()
    image_path.write_bytes(payload.replace(b"\n\n", b'\nspace units: "mm" "mm" "mm"\n\n', 1))
    data = analytical_case().model_dump(mode="json")
    data["source_image"] = ImageReference(uri="ct.nrrd").model_dump(mode="json")
    case = CorridorCase.model_validate(data)
    case_path = tmp_path / "case.json"
    write_case(case_path, case)

    window = MainWindow()
    window.open_case(case_path)
    assert window.viewer.volume.shape == (11, 10, 9)
    assert [view.slider.maximum() for view in window.viewer.views] == [8, 9, 10]
    window.viewer.views[0].slider.setValue(2)
    app.processEvents()
    assert window.viewer.views[0].image_item.image is not None
    window.close()
