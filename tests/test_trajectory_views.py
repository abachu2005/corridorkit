"""Physical geometry and actual Qt handles; no GL context or clinical claims."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtCore, QtWidgets

from corridorkit.desktop.trajectory_geometry import (
    clip_segment_to_slab,
    feasible_paths,
    project_segment,
    resample_path_plane,
)
from corridorkit.desktop.views import LinkedViewer
from corridorkit.domain.models import ApproachResult, CaseResult, TrajectoryResult
from corridorkit.io.volumes import Volume
from corridorkit.synthetic.cases import analytical_case


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def witness_result(case):
    paths = [
        TrajectoryResult(
            direction=(0, 0, 1), feasible=False, entry_point_mm=(1, 2, 3),
            working_depth_mm=9, reason="collision",
        ),
        TrajectoryResult(
            direction=(0, 0.6, 0.8), feasible=True, entry_point_mm=(2, 3, 4),
            working_depth_mm=10, reached_point_indices=[0, 1],
        ),
        TrajectoryResult(
            direction=(1, 0, 0), feasible=True, entry_point_mm=(4, 5, 6),
            working_depth_mm=8, reached_point_indices=[2],
        ),
    ]
    approach = ApproachResult(
        name="EEA", kind="eea", status="complete", target_count=4,
        reached_point_indices=[0, 1, 2], reached_measure_mm3=None,
        feasible_trajectory_count=2, feasible_solid_angle_sr=None,
        witness_direction=(0, 0.6, 0.8), witness_entry_point_mm=(2, 3, 4),
        best_working_depth_mm=10, minimum_clearance_mm=None, trajectories=paths,
    )
    return CaseResult(case_id=case.case_id, approaches=[approach])


def source():
    affine = np.diag([1.0, 2.0, 2.0, 1.0])
    affine[:3, 3] = [-5, -5, -4]
    return Volume(np.indices((32, 24, 24)).sum(axis=0).astype(float), affine, "HU")


def test_projection_uses_ras_mm_and_finite_endpoints():
    np.testing.assert_allclose(
        project_segment([-15, 20, 40], [30, 60, 80], (0, 2)),
        [[-15, 40], [30, 80]],
    )


@pytest.mark.parametrize("entry,tip,expected", [
    ([0, 0, 0], [10, 20, 10], [[4, 8, 4], [6, 12, 6]]),
    ([10, 20, 10], [0, 0, 0], [[6, 12, 6], [4, 8, 4]]),
    ([0, 0, 5], [10, 20, 5], [[0, 0, 5], [10, 20, 5]]),
    ([0, 0, 2], [10, 20, 2], None),
    ([0, 0, 0], [0, 0, 3], None),
    ([0, 0, 6], [0, 0, 9], [[0, 0, 6], [0, 0, 6]]),
])
def test_finite_slab_clipping(entry, tip, expected):
    actual = clip_segment_to_slab(entry, tip, 2, 5, 2)
    if expected is None:
        assert actual is None
    else:
        np.testing.assert_allclose(actual, expected)


@pytest.mark.parametrize("linear", [
    [[-1, 0, 0], [0, 2, 0], [0, 0, 3]],
    [[0, -2, 0.3], [1, 0, 0.2], [0, 0, 1.5]],
    [[0.8, -1.2, 0.3], [0.6, 1.6, 0.1], [0.2, 0.2, 1.7]],
])
def test_oblique_linear_interpolation_native_arbitrary_affine(linear):
    affine = np.eye(4)
    affine[:3, :3] = linear
    affine[:3, 3] = [-8, 3, 11]
    volume = Volume(np.zeros((20, 21, 22)), affine)
    indices = np.moveaxis(np.indices(volume.data.shape), 0, -1)
    world = volume.index_to_world(indices)
    volume.data = 3 * world[..., 0] - 2 * world[..., 1] + 0.5 * world[..., 2] + 12
    entry = volume.index_to_world([6, 8, 9])
    tip = volume.index_to_world([14, 13, 12])
    plane = resample_path_plane(
        volume, entry, tip, spacing_mm=0.7, margin_mm=3, half_width_mm=4,
    )
    sample_world = (
        entry + plane.along_mm[None, :, None] * plane.along_direction
        + plane.across_mm[:, None, None] * plane.across_direction
    )
    expected = 3 * sample_world[..., 0] - 2 * sample_world[..., 1] + 0.5 * sample_world[..., 2] + 12
    finite = np.isfinite(plane.image)
    assert finite.any()
    np.testing.assert_allclose(plane.image[finite], expected[finite], atol=2e-5)
    assert np.dot(plane.along_direction, plane.across_direction) == pytest.approx(0, abs=1e-12)
    np.testing.assert_allclose(entry + np.linalg.norm(tip - entry) * plane.along_direction, tip)


def test_oblique_fov_background_and_bounded_memory():
    volume = Volume(np.ones((5, 5, 5), dtype=np.int16) * 400, np.eye(4), "HU")
    sample = resample_path_plane(
        volume, [2, 2, 1], [2, 2, 3], margin_mm=5, half_width_mm=5, max_pixels=31,
    )
    assert np.isnan(sample.image).any()
    assert np.nanmin(sample.image) == np.nanmax(sample.image) == 400
    assert max(sample.image.shape) <= 31


def test_feasible_witness_excludes_nominal_missing_conditional_and_incomplete():
    case = analytical_case()
    result = witness_result(case).approaches[0]
    paths = feasible_paths(result, 80)
    assert [p.trajectory_index for p in paths] == [1, 2]
    np.testing.assert_allclose(paths[0].entry_mm, [2, 3, 4])
    np.testing.assert_allclose(paths[0].tip_mm, [2, 9, 12])
    for updates in (
        {"conditional": True}, {"entry_point_mm": None}, {"working_depth_mm": None},
        {"working_depth_mm": 90}, {"direction": (0, 0, 2)},
    ):
        modified = result.model_copy(update={
            "trajectories": [result.trajectories[1].model_copy(update=updates)],
        })
        assert feasible_paths(modified, 80) == []
    assert feasible_paths(result.model_copy(update={"status": "incomplete"}), 80) == []


def test_actual_view_handles_selection_focus_and_projection(app):
    viewer = LinkedViewer()
    case = analytical_case()
    viewer.set_case(case)
    viewer.set_volume(source())
    viewer.set_result(witness_result(case))
    assert viewer.selected_trajectory.trajectory_index == 1
    np.testing.assert_allclose(viewer.selected_trajectory.tip_mm, [2, 9, 12])
    np.testing.assert_allclose(viewer.crosshair, [2, 9, 12])
    assert viewer.detail_tabs.widget(0) is viewer.three_d
    assert viewer.detail_tabs.widget(1) is viewer.path_view
    assert viewer.path_view.sample is not None
    assert viewer.path_view.image_item.image is not None
    assert "RAS" in viewer.path_view.heading.toolTip()
    assert "insertion" in viewer.path_view.heading.text()
    assert "mm" in viewer.path_view.plot.getAxis("bottom").labelUnits
    assert "Entry" in viewer.path_view.trajectory_items
    assert "Tip" in viewer.path_view.trajectory_items
    assert viewer.three_d.selected_trajectory is viewer.selected_trajectory
    for view in viewer.views:
        handles = view.trajectory_items
        assert handles["projection"].opts["pen"].style() == QtCore.Qt.PenStyle.DashLine
        assert handles["intersection"].opts["pen"].style() == QtCore.Qt.PenStyle.SolidLine
        assert handles["tip_label"].toPlainText() == "Tip in slab"
        assert "shaft_cap_0" in handles
        assert handles["shaft_cap_0"].opts["pen"].style() == QtCore.Qt.PenStyle.DashLine
    axial = viewer.views[0]
    assert axial.trajectory_items["entry_label"].toPlainText() == "Entry projected"
    viewer.set_crosshair([2, 9, 30])
    assert "intersection" not in axial.trajectory_items
    assert "projection" in axial.trajectory_items
    assert viewer.select_trajectory("EEA", 2).trajectory_index == 2
    np.testing.assert_allclose(viewer.selected_trajectory.tip_mm, [12, 5, 6])
    assert viewer.focus_selected_trajectory()
    viewer.window.setValue(800)
    np.testing.assert_allclose(viewer.path_view.image_item.getLevels(),
                               [viewer.level.value() - 400, viewer.level.value() + 400])
    assert viewer.select_trajectory("EEA", 0) is None
    assert viewer.path_view.image_item.image is None
    assert all(not v.trajectory_items for v in viewer.views)
    viewer.close()


def test_results_clear_on_edits_hidden_unknown_and_invalid_results(app):
    viewer = LinkedViewer()
    case = analytical_case()
    result = witness_result(case)
    viewer.set_case(case)
    viewer.set_volume(source())
    viewer.set_result(result)
    viewer.set_case(case, [])
    assert viewer.selected_trajectory is None
    viewer.set_case(case)
    viewer.set_result(result)
    viewer.set_coverage_labels(["eea_only", "both", "unreached", "unavailable"])
    assert viewer.coverage_labels[-1] == "unavailable"
    assert viewer.three_d.coverage_labels[-1] == "unavailable"
    viewer.set_result(None)
    assert viewer.coverage_labels is None
    assert viewer.three_d.coverage_labels is None
    assert viewer.selected_trajectory is None
    viewer.set_result(result)
    unknown = result.model_copy(update={
        "approaches": [result.approaches[0].model_copy(update={"status": "incomplete"})],
    })
    viewer.set_result(unknown)
    assert viewer.selected_trajectory is None
    assert viewer.path_view.image_item.image is None
    viewer.set_result(result)
    with pytest.raises(ValueError, match="active case"):
        viewer.set_result(result.model_copy(update={"case_id": "wrong"}))
    assert viewer.result is None
    assert all(not v.trajectory_items for v in viewer.views)
    viewer.set_result(result)
    viewer.set_case(case.model_copy(update={
        "target": case.target.model_copy(update={"points_mm": [(1, 2, 3)]}),
    }))
    assert viewer.selected_trajectory is None
    assert viewer.path_view.image_item.image is None
    viewer.close()


def test_default_ranking_matches_engine_and_no_source_stays_explicit(app):
    viewer = LinkedViewer()
    case = analytical_case()
    result = witness_result(case)
    approach = result.approaches[0]
    trajectories = list(approach.trajectories)
    trajectories[2] = trajectories[2].model_copy(update={
        "reached_point_indices": [0, 1], "minimum_clearance_mm": 20,
    })
    trajectories[1] = trajectories[1].model_copy(update={"minimum_clearance_mm": 3})
    result = result.model_copy(update={
        "approaches": [approach.model_copy(update={"trajectories": trajectories})],
    })
    viewer.set_case(case)
    viewer.set_result(result)
    assert viewer.selected_trajectory.trajectory_index == 2
    assert "load source CT" in viewer.path_view.heading.text()
    assert viewer.path_view.image_item.image is None
    assert not viewer.focus_selected_trajectory()
    viewer.set_volume(source())
    assert viewer.selected_trajectory is None  # A new source invalidates results.
    viewer.close()


def test_native_scene_selected_path_uses_actual_endpoints(app):
    from unittest.mock import MagicMock

    viewer = LinkedViewer()
    case = analytical_case()
    viewer.set_case(case)
    viewer.set_result(witness_result(case))
    three = viewer.three_d
    three.available = True
    three._vtk = MagicMock()
    three.renderer = MagicMock()
    three._vtk_widget = MagicMock()
    three._shaft = MagicMock()
    three._sphere = MagicMock()
    three._actor = MagicMock()
    three.refresh()
    selected = three._shaft.call_args_list[-1].args
    np.testing.assert_allclose(selected[0], [2, 3, 4])
    np.testing.assert_allclose(selected[1], [0, 0.6, 0.8])
    assert selected[2] == 10
    assert selected[-2:] == ((58, 166, 255), 1.0)
    viewer.close()
