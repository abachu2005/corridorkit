import os
import importlib.util
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytestmark = pytest.mark.skipif(importlib.util.find_spec("PySide6") is None,
                               reason="optional desktop")


@pytest.fixture
def gui(monkeypatch):
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                        lambda *a: pytest.fail(f"Unexpected GUI error: {a[-1]}"))
    return app


def wait(gui, window):
    from PySide6 import QtCore
    timer = QtCore.QElapsedTimer()
    timer.start()
    while window.thread is not None and timer.elapsed() < 10000:
        gui.processEvents()
    assert window.thread is None


def test_real_ct_import_target_edit_save_reopen_and_abstain(gui, tmp_path):
    import nibabel as nib
    from skullbase_corridor.desktop.app import MainWindow
    path = tmp_path / "ct.nii.gz"
    image = nib.Nifti1Image(np.zeros((20, 22, 24), np.int16), np.diag([1, 2, 3, 1]))
    image.header.set_xyzt_units("mm")
    nib.save(image, path)
    window = MainWindow()
    window.import_ct(path)
    assert window.viewer.volume is not None
    assert window.case.target.source == "unreviewed_center_placeholder"
    initial = window.case.target.points_mm
    window.pick_mode.setCurrentIndex(1)
    window._point_picked((8, 12, 18))
    assert window.case.target.points_mm == [(8, 12, 18)]
    window.undo_stack.undo()
    assert window.case.target.points_mm == initial
    window.case_path = tmp_path / "case.json"
    window.save_case()
    window.open_case(window.case_path)
    assert window.document is not None
    window.start_analysis()
    wait(gui, window)
    assert all(a.status.value == "abstained" for a in window.result.approaches)
    assert window.results.item(0, 2).text() == "Unavailable"
    assert window.results.item(0, 3).text() == "—"
    window.open_synthetic()
    wait(gui, window)
    assert window.viewer.volume is None
    window.close()


def test_cancel_interrupts_and_no_stale_result(gui):
    from skullbase_corridor.desktop.app import MainWindow
    window = MainWindow()
    window.open_synthetic()
    window.cancel_analysis()
    wait(gui, window)
    assert window.result is None
    assert not window.export_action.isEnabled()
    window.close()


def test_mask_stroke_undo_invalidates_review(gui):
    from skullbase_corridor.desktop.app import MainWindow
    from skullbase_corridor.desktop.editing import MaskEditor
    from skullbase_corridor.io.volumes import Volume
    window = MainWindow()
    window.open_synthetic()
    wait(gui, window)
    source = Volume(np.zeros((10, 10, 10)), np.eye(4))
    window.viewer.set_volume(source)
    window.mask_editor = MaskEditor(source)
    window.document.approve_anatomy("Test reviewer")
    window.pick_mode.setCurrentIndex(4)
    window._point_picked((5, 5, 5))
    assert window.mask_editor.volume.data.sum() > 0
    count = window.undo_stack.count()
    window._point_picked((5, 5, 5))
    assert window.undo_stack.count() == count
    assert window.result is None
    assert not window.document.anatomy_approved
    window.undo_stack.undo()
    assert not window.mask_editor.volume.data.any()
    window.undo_stack.redo()
    assert window.mask_editor.volume.data.sum() > 0
    window.close()


def test_pending_mask_blocks_case_save(gui, tmp_path, monkeypatch):
    from PySide6 import QtWidgets
    from skullbase_corridor.desktop.app import MainWindow
    warnings = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a: warnings.append(a[-1]))
    window = MainWindow()
    window.open_synthetic()
    wait(gui, window)
    window.case_path = tmp_path / "case.json"
    window._mask_pending = True
    window.save_case()
    assert not window.case_path.exists()
    assert warnings
    window.close()


def test_same_ct_layers_survive_configuration_but_follow_anatomy(gui, tmp_path):
    import nibabel as nib
    from skullbase_corridor.desktop.app import MainWindow
    from skullbase_corridor.domain.models import CorridorCase
    from skullbase_corridor.io.volumes import Volume
    path = tmp_path / "ct.nii.gz"
    image = nib.Nifti1Image(np.zeros((10, 10, 10), np.int16), np.eye(4))
    image.header.set_xyzt_units("mm")
    nib.save(image, path)
    mask_path = tmp_path / "mask.npy"
    np.save(mask_path, np.ones((10, 10, 10), np.uint8))
    window = MainWindow()
    window.import_ct(path)
    window.viewer.set_mri_overlay(Volume(np.ones((10, 10, 10)), np.eye(4)))
    data = window.case.model_dump(mode="json")
    data["protected_structures"].append({
        "name": "test-mask", "geometry": {"kind": "voxel", "uri": str(mask_path),
                                       "affine": np.eye(4).tolist()}})
    window._set_case(CorridorCase.model_validate(data), from_history=True)
    assert window.viewer.mri_overlay is not None
    assert "test-mask" in window.viewer.masks
    data["protected_structures"].pop()
    window._set_case(CorridorCase.model_validate(data), from_history=True)
    assert "test-mask" not in window.viewer.masks
    assert window.viewer.mri_overlay is not None
    data["case_id"] = "different-case-same-ct"
    window._set_case(CorridorCase.model_validate(data), from_history=True)
    assert window.viewer.source_volume is not None
    window.close()
