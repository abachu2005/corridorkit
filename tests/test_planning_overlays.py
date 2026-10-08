"""Display geometry regression tests: physical coordinates, not screen pixels."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtCore, QtWidgets
import pyqtgraph as pg

from skullbase_corridor.desktop.planning_overlays import (
    CATEGORY_CODES,
    CATEGORY_COLORS,
    capsule_contains,
    capsule_distance,
    draw_instrument,
    draw_portal,
    draw_target_slice,
    projected_capsule,
    projected_portal,
    sample_capsule_slice,
    sample_target_slice,
)
from skullbase_corridor.domain.models import SampledMaskTarget, TargetPointCloud
from skullbase_corridor.io.masks import target_from_mask


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.parametrize("axes", [(0, 1), (0, 2), (2, 1)])
def test_rotated_portal_has_correct_physical_ellipse(axes):
    center = np.array([13., -4., 21.])
    normal = np.array([1., 2., 3.])
    normal /= np.linalg.norm(normal)
    rim = projected_portal(center, normal * 7, 6, axes, samples=4097)
    centered = rim[:-1] - center[list(axes)]
    covariance = centered.T @ centered / len(centered)
    radii = np.sqrt(2 * np.linalg.eigvalsh(covariance))
    omitted = next(a for a in range(3) if a not in axes)
    np.testing.assert_allclose(radii, [6 * abs(normal[omitted]), 6], atol=1e-10)
    np.testing.assert_allclose(rim[0], rim[-1])


def test_edge_on_portal_is_line_not_circle():
    rim = projected_portal([5, 6, 7], [1, 0, 0], 4, (0, 1))
    np.testing.assert_allclose(rim[:, 0], 5)
    assert np.ptp(rim[:, 1]) == pytest.approx(8)
    with pytest.raises(ValueError):
        projected_portal([0, 0, 0], [0, 0, 0], 2, (0, 1))


def test_finite_capsule_caps_and_degenerate_sphere():
    entry, tip = [0, 0, 0], [0, 0, 10]
    query = [[2, 0, 5], [0, 0, -2], [1, 0, -2], [0, 0, 12], [0, 0, 13]]
    assert capsule_contains(query, entry, tip, 2).tolist() == [True, True, False, True, False]
    np.testing.assert_allclose(capsule_distance(query, entry, tip),
                               [2, 2, np.sqrt(5), 2, 3])
    assert capsule_contains([[0, 0, 1], [0, 0, 2]], entry, entry, 1).tolist() == [True, False]


def test_capsule_slice_uses_actual_plane_not_projected_radius():
    raster = sample_capsule_slice([0, 0, 0], [0, 0, 10], 2, (0, 1), 11,
                                   spacing=.05)
    world = raster.world_points((0, 1), 11)
    expected = world[..., 0] ** 2 + world[..., 1] ** 2 <= 3
    np.testing.assert_array_equal(raster.values, expected)
    area = raster.values.sum() * .05 ** 2
    assert area == pytest.approx(3 * np.pi, rel=.02)
    assert sample_capsule_slice([0, 0, 0], [0, 0, 10], 2, (0, 1), 12.1) is None
    assert sample_capsule_slice([0, 0, 0], [0, 0, 10], 2, (0, 1), -2) is None


def test_parallel_oblique_capsule_and_clipped_bounds():
    entry, tip = [0, 0, 1], [8, 5, 1]
    raster = sample_capsule_slice(entry, tip, 2, (0, 1), 0, bounds=(1, 7, 0, 4),
                                   spacing=.01, max_pixels=64)
    assert raster.values.shape == (64, 64)
    np.testing.assert_allclose(raster.bounds, [1, 7, 0, 4])
    xy = raster.world_points((0, 1), 0)[..., :2]
    delta = np.array([8, 5])
    t = np.clip(xy @ delta / (delta @ delta), 0, 1)
    expected = np.sum((xy - t[..., None] * delta) ** 2, axis=-1) <= 3
    np.testing.assert_array_equal(raster.values, expected)
    assert sample_capsule_slice(entry, tip, 2, (0, 1), 4) is None


def test_projected_capsule_radius_is_physical():
    rim = projected_capsule([10, 20, -500], [16, 20, 900], 3, (0, 1))
    np.testing.assert_allclose(rim.min(0), [7, 17])
    np.testing.assert_allclose(rim.max(0), [19, 23])
    np.testing.assert_allclose(rim[0], rim[-1])


def test_oblique_shaft_cross_section_is_ellipse_in_actual_plane():
    # A long shaft tilted 45 degrees from the slice normal intersects z=0 in
    # x²/2 + y² <= r²; neither a projected capsule nor a radius-r circle.
    raster = sample_capsule_slice([-10, 0, -10], [10, 0, 10], 2, (0, 1), 0,
                                   spacing=.08)
    world = raster.world_points((0, 1), 0)
    expected = world[..., 0] ** 2 / 2 + world[..., 1] ** 2 <= 4
    np.testing.assert_array_equal(raster.values, expected)
    assert np.max(np.abs(world[..., 0][raster.values])) > 2.7
    # The same finite shaft has no section far beyond its end.
    assert sample_capsule_slice([-10, 0, -10], [10, 0, 10], 2, (0, 1), 13) is None


def test_mask_categories_preserve_holes_and_disconnected_voxels():
    mask = np.zeros((5, 3, 2), dtype=np.uint8)
    mask[0, 1, 0] = mask[4, 1, 0] = 1
    affine = np.diag([2., 3., 4., 1.])
    affine[:3, 3] = [10, 20, 30]
    target = target_from_mask(mask, affine)
    raster = sample_target_slice(target, ["eea_only", "tm_only"], (0, 1), 30, 1)
    world = raster.world_points((0, 1), 30)
    first = (world[..., 0] >= 9) & (world[..., 0] < 11)
    last = (world[..., 0] >= 17) & (world[..., 0] < 19)
    assert np.all(raster.values[first] == CATEGORY_CODES["eea_only"])
    assert np.all(raster.values[last] == CATEGORY_CODES["tm_only"])
    assert not raster.values[~(first | last)].any()
    np.testing.assert_allclose(raster.bounds, [9, 19, 21.5, 24.5])
    assert sample_target_slice(target, None, (0, 1), 40, 1) is None


@pytest.mark.parametrize("axes", [(0, 1), (1, 2), (2, 0)])
def test_arbitrary_affine_categories_match_native_nearest_voxel(axes):
    # Includes rotation, shear, anisotropic scale, reflection and translation.
    affine = np.array([[0, -2, .4, 13], [1.5, .3, .2, -8],
                       [.2, .4, -3, 21], [0, 0, 0, 1.]])
    mask = np.ones((4, 3, 3), dtype=np.uint8)
    mask[1, 1, :] = 0
    target = target_from_mask(mask, affine)
    names = ["eea_only", "tm_only", "both", "unreached", "unavailable"]
    categories = [names[i % len(names)] for i in range(len(target.points_mm))]
    axis = next(a for a in range(3) if a not in axes)
    coordinate = target.array().mean(0)[axis]
    raster = sample_target_slice(target, categories, axes, coordinate, [.2, .3, .4], 64)
    inverse = np.linalg.inv(affine)
    native = raster.world_points(axes, coordinate) @ inverse[:3, :3].T + inverse[:3, 3]
    nearest = np.floor(native + .5).astype(int)
    expected_volume = np.zeros(mask.shape, dtype=np.uint8)
    for index, category in zip(np.argwhere(mask), categories):
        expected_volume[tuple(index)] = CATEGORY_CODES[category]
    expected = np.zeros(raster.values.shape, dtype=np.uint8)
    for row in range(expected.shape[0]):
        for column in range(expected.shape[1]):
            index = nearest[row, column]
            if np.all(index >= 0) and np.all(index < mask.shape):
                expected[row, column] = expected_volume[tuple(index)]
    np.testing.assert_array_equal(raster.values, expected)
    assert expected.any()


def test_sparse_target_never_fabricates_mask(app):
    cloud = TargetPointCloud(points_mm=[(0, 0, 0), (10, 10, 10)])
    sampled = target_from_mask(np.ones((3, 3, 3)), np.eye(4), stride=2)
    plot = pg.PlotWidget()
    for target in (cloud, sampled):
        assert sample_target_slice(target, None, (0, 1), 0, 1) is None
        assert draw_target_slice(plot, target, None, (0, 1), 0, 1) == []
    assert not plot.listDataItems()
    plot.close()


def test_huge_declared_shape_allocates_only_plane():
    target = SampledMaskTarget(
        points_mm=[(100., 200., 300.)], affine=tuple(map(tuple, np.eye(4))),
        source_shape=(10**9, 10**9, 10**9), voxel_volume_mm3=1,
    )
    raster = sample_target_slice(target, None, (0, 1), 300, .0001, max_pixels=32)
    assert raster.values.shape == (32, 32)
    assert np.all(raster.values == CATEGORY_CODES["target"])


def test_invalid_full_mask_metadata_is_not_silently_rounded():
    target = target_from_mask(np.ones((2, 2, 2)), np.eye(4))
    shifted = target.model_copy(update={"points_mm": [(0.2, 0, 0)]})
    with pytest.raises(ValueError, match="voxel centres"):
        sample_target_slice(shifted, None, (0, 1), 0, 1)
    with pytest.raises(ValueError, match="category"):
        sample_target_slice(target, ["both"], (0, 1), 0, 1)
    repeated = target.model_copy(update={"points_mm": [(0, 0, 0), (0, 0, 0)]})
    with pytest.raises(ValueError, match="unique"):
        sample_target_slice(repeated, None, (0, 1), 0, 1)


def test_qt_items_labels_transform_colors_and_cleanup(app):
    plot = pg.PlotWidget()
    portal = draw_portal(plot, [10, 20, 30], [1, 0, 1], 4, (0, 1))
    assert "projected" in portal[1].toPlainText()
    out = draw_instrument(plot, [0, 0, 0], [0, 0, 5], 2, (0, 1), 20)
    assert [item.planning_kind for item in out] == [
        "instrument_projection", "instrument_projection_label"]
    assert out[0].opts["pen"].style() == QtCore.Qt.PenStyle.DashLine
    assert "projected" in out[1].toPlainText()
    inside = draw_instrument(plot, [0, 0, 0], [0, 0, 5], 2, (0, 1), 3)
    assert [item.planning_kind for item in inside[:2]] == [
        "instrument_slice", "instrument_slice_outline"]
    target = target_from_mask(np.ones((2, 2, 2)), np.diag([2., 3., 4., 1.]))
    items = draw_target_slice(plot, target, None, (0, 1), 0, 1)
    assert len(items) == 2
    image, outline = items
    np.testing.assert_array_equal(np.unique(image.image.reshape(-1, 4), axis=0),
                                  [CATEGORY_COLORS["target"]])
    rect = image.mapRectToParent(image.boundingRect())
    np.testing.assert_allclose([rect.left(), rect.right(), rect.top(), rect.bottom()],
                               [-1, 3, -1.5, 4.5])
    outline.generatePath()
    contour_rect = outline.mapRectToParent(outline.path.boundingRect())
    np.testing.assert_allclose(
        [contour_rect.left(), contour_rect.right(), contour_rect.top(), contour_rect.bottom()],
        [-1, 3, -1.5, 4.5],
    )
    for item in portal + out + inside + items:
        assert item.scene() is not None
        plot.removeItem(item)
        assert item.scene() is None
    plot.close()


def test_all_category_rgba_values_are_discrete_and_outlined(app):
    target = target_from_mask(np.ones((5, 2, 1)), np.eye(4))
    names = ["eea_only", "tm_only", "both", "unreached", "unavailable"]
    categories = [name for name in names for _ in range(2)]
    plot = pg.PlotWidget()
    items = draw_target_slice(plot, target, categories, (0, 1), 0, .25)
    assert len(items) == 6
    actual = set(map(tuple, np.unique(items[0].image.reshape(-1, 4), axis=0)))
    assert actual == {CATEGORY_COLORS[name] for name in names}
    assert all(isinstance(item, pg.IsocurveItem) for item in items[1:])
    plot.close()
