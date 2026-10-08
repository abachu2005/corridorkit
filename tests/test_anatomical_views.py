import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtWidgets
from PySide6.QtTest import QSignalSpy

from corridorkit.desktop.views import LinkedViewer, in_slice
from corridorkit.io.volumes import Volume
from corridorkit.synthetic.cases import analytical_case


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def source():
    affine = np.diag([-1.0, -2.0, 3.0, 1.0])
    affine[:3, 3] = [10, 20, 30]
    return Volume(np.arange(5 * 6 * 7).reshape(5, 6, 7), affine)


def test_true_slices_patient_coordinates_and_no_vtk_context(app):
    viewer = LinkedViewer()
    viewer.set_case(analytical_case())
    native = source()
    viewer.set_volume(native)
    assert not viewer.three_d.available and viewer.three_d._vtk_widget is None
    assert [v.slider.maximum() for v in viewer.views] == [6, 5, 4]
    viewer.set_crosshair([8, 16, 36])
    assert [v.slider.value() for v in viewer.views] == [2, 3, 2]
    np.testing.assert_array_equal(viewer.views[0].image_item.image, native.data[::-1, ::-1, 2].T)
    assert "36.00 mm" in viewer.views[0].heading.text()
    # Pixel centre x=8,y=16 appears at physical 8,16, not edge 8.5,17.
    rect = viewer.views[0].image_item.boundingRect()
    mapped = viewer.views[0].image_item.mapRectToParent(rect)
    assert mapped.left() == pytest.approx(5.5)
    assert mapped.right() == pytest.approx(10.5)
    assert "unknown" in viewer.source_label.text()
    viewer.close()


def test_click_to_crosshair_and_target_portal_signals(app):
    viewer = LinkedViewer()
    viewer.set_volume(source())
    picked = QSignalSpy(viewer.point_picked)
    targets = QSignalSpy(viewer.target_picked)
    portals = QSignalSpy(viewer.portal_picked)
    viewer.set_pick_mode("target")
    viewer.pick_in_view(0, 8.2, 15.8)
    assert picked.count() == targets.count() == 1
    np.testing.assert_allclose(targets.at(0)[0][:2], [8.2, 15.8])
    np.testing.assert_allclose(viewer.crosshair[:2], [8, 16])
    viewer.set_pick_mode("portal")
    viewer.pick_in_view(1, 9, 39)
    assert portals.count() == 1
    viewer.pick_in_view(0, 1000, 1000)
    assert picked.count() == 2
    viewer.close()


def test_mask_overlay_mm_editor_and_undo_signal(app):
    viewer = LinkedViewer()
    viewer.set_volume(source())
    changed = QSignalSpy(viewer.mask_changed)
    assert viewer.edit_mask("target", [8, 16, 39], 2.1, 3) > 0
    assert changed.count() == 1
    assert "target" in viewer.views[0].mask_items
    before = viewer.masks["target"].data.copy()
    assert viewer.undo_mask("target")
    assert not viewer.masks["target"].data.any()
    assert viewer.redo_mask("target")
    np.testing.assert_array_equal(viewer.masks["target"].data, before)
    assert changed.count() == 3
    viewer.opacity.setValue(20)
    assert viewer.views[0].mask_items["target"].opacity() == 0.2
    viewer.set_mask("target", None)
    assert not viewer.views[0].mask_items
    viewer.close()


def test_new_case_and_failed_load_clear_source(app, tmp_path):
    viewer = LinkedViewer()
    case = analytical_case()
    viewer.set_case(case)
    viewer.set_volume(source())
    viewer.set_case(case, [])
    assert viewer.volume is not None and not viewer.visible
    viewer.set_case(case.model_copy(update={"case_id": "another"}))
    assert viewer.volume is None
    assert all(v.image_item.image is None for v in viewer.views)
    viewer.set_volume(source())
    with pytest.raises(ValueError):
        viewer.load_volume(tmp_path / "unsupported.img")
    assert viewer.volume is None and viewer.three_d.volume is None
    viewer.close()


def test_out_of_plane_points_are_not_projected(app):
    assert in_slice([[1, 2, 3], [1, 2, 30]], 2, 3, 1).tolist() == [True, False]
    viewer = LinkedViewer()
    case = analytical_case()
    target = case.target.model_copy(update={"points_mm": [(8.0, 16.0, 36.0), (8.0, 16.0, 48.0)]})
    viewer.set_case(case.model_copy(update={"target": target, "protected_structures": []}), [])
    viewer.set_volume(source())
    viewer.set_crosshair([8, 16, 36])
    assert len(viewer.views[0].overlays) == 1
    x, y = viewer.views[0].overlays[0].getData()
    assert len(x) == len(y) == 1
    viewer.close()


def test_reject_lps_case_without_silent_overlay_conversion(app):
    viewer = LinkedViewer()
    case = analytical_case().model_copy(update={"coordinate_frame": "LPS"})
    with pytest.raises(ValueError, match="RAS"):
        viewer.set_case(case)
    viewer.close()


def test_result_witness_uses_actual_entry_and_finite_depth_without_vtk(app):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    viewer = LinkedViewer()
    case = analytical_case()
    viewer.set_case(case)
    approach = case.approaches[0]
    witness = SimpleNamespace(
        name=approach.name,
        witness_direction=(0.0, 0.0, 1.0),
        witness_entry_point_mm=(3.0, 4.0, 5.0),
        best_working_depth_mm=12.0,
        reached_point_indices=[0],
    )
    result = SimpleNamespace(case_id=case.case_id, approaches=[witness], simultaneous_pairs=[])
    viewer.set_result(result)
    three = viewer.three_d
    # Exercise scene assembly with spies, never import VTK or create a GL context.
    three.available = True
    three._vtk = MagicMock()
    three.renderer = MagicMock()
    three._vtk_widget = MagicMock()
    three._shaft = MagicMock()
    three._sphere = MagicMock()
    three._actor = MagicMock()
    three.refresh()
    witness_call = three._shaft.call_args_list[-1].args
    assert witness_call[0] == (3.0, 4.0, 5.0)
    assert witness_call[1] == (0.0, 0.0, 1.0)
    assert witness_call[2] == 12.0
    viewer.set_result(None)
    assert three.result is None
    viewer.close()
