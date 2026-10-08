"""Physical-mm planning overlays; geometry functions do not import Qt.

``draw_portal(plot, center, normal, radius, axes, ...)``,
``draw_instrument(plot, entry, tip, radius, axes, coordinate, bounds=None, ...)``,
and ``draw_target_slice(plot, target, categories, axes, coordinate, spacing,
max_pixels=512)`` add graphics items and return *all* added items for removal.
Axes are the two displayed world-coordinate axes, in horizontal/vertical order.
Bounds are pixel-edge bounds (xmin, xmax, ymin, ymax), in physical mm.

Solid translucent instrument pixels are the finite capsule evaluated on the
actual plane, not a thick slab. Dashed silhouettes and portal outlines are
explicitly labelled projections. Raster boundaries have finite pixel resolution;
the capsule membership predicate itself is exact (no infinite-cylinder shortcut).

Full-mask targets use a sparse representation of the declared native label
volume: missing voxels are background, never interpolated foreground. No dense
source_shape allocation, hull, or surface is made from sparse point clouds.
All coordinates must already be in the same physical frame as the plot.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np

from corridorkit.domain.models import SampledMaskTarget


CATEGORY_COLORS = {
    "target": (255, 220, 65, 95),
    "eea_only": (58, 166, 255, 100),
    "tm_only": (240, 154, 62, 100),
    "both": (186, 112, 245, 110),
    "unreached": (150, 155, 165, 85),
    "unavailable": (235, 220, 155, 85),
}
CATEGORY_CODES = {name: index + 1 for index, name in enumerate(CATEGORY_COLORS)}
INSTRUMENT_COLOR = (66, 225, 164)


def _axes(axes):
    axes = tuple(axes)
    if len(axes) != 2 or len(set(axes)) != 2 or any(a not in (0, 1, 2) for a in axes):
        raise ValueError("axes must be two distinct physical coordinate axes")
    return axes, next(a for a in range(3) if a not in axes)


def _point(value):
    value = np.asarray(value, dtype=float)
    if value.shape != (3,) or not np.isfinite(value).all():
        raise ValueError("physical points must be finite triples")
    return value


def _radius(radius):
    radius = float(radius)
    if not np.isfinite(radius) or radius < 0:
        raise ValueError("radius must be finite and nonnegative")
    return radius


def projected_portal(center, normal, radius, axes, samples=129):
    """Closed projected disk rim, shape (samples, 2), in physical mm.

    The principal radii are r and r*abs(normal[omitted_axis]); an edge-on
    disk is a line, not a fabricated circle.
    """
    axes, _ = _axes(axes)
    center, normal, radius = _point(center), _point(normal), _radius(radius)
    norm = np.linalg.norm(normal)
    if norm <= 1e-12 or samples < 5:
        raise ValueError("normal must be nonzero and samples must be at least five")
    normal = normal / norm
    reference = np.eye(3)[np.argmin(np.abs(normal))]
    u = np.cross(normal, reference)
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    theta = np.linspace(0, 2 * np.pi, samples)
    rim = center + radius * (np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v)
    return rim[:, axes]


def capsule_distance(points, entry, tip):
    """Euclidean distance to the *finite* centre segment, for (..., 3) points."""
    entry, tip = _point(entry), _point(tip)
    points = np.asarray(points, dtype=float)
    if points.shape[-1:] != (3,) or not np.isfinite(points).all():
        raise ValueError("query points must have final dimension three and be finite")
    delta = tip - entry
    length2 = float(delta @ delta)
    t = np.zeros(points.shape[:-1]) if length2 == 0 else np.clip(
        ((points - entry) @ delta) / length2, 0, 1,
    )
    return np.linalg.norm(points - (entry + t[..., None] * delta), axis=-1)


def capsule_contains(points, entry, tip, radius):
    """Exact capsule membership, including spherical end caps."""
    return capsule_distance(points, entry, tip) <= _radius(radius)


def projected_capsule(entry, tip, radius, axes, samples=65):
    """Closed orthographic silhouette of the finite capsule, in physical mm."""
    axes, _ = _axes(axes)
    ends = np.array([_point(entry), _point(tip)])[:, axes]
    radius = _radius(radius)
    if samples < 3:
        raise ValueError("samples must be at least three")
    delta = ends[1] - ends[0]
    angle = np.arctan2(delta[1], delta[0])
    theta = angle + np.linspace(-np.pi / 2, np.pi / 2, samples)
    front = ends[1] + radius * np.column_stack((np.cos(theta), np.sin(theta)))
    theta += np.pi
    back = ends[0] + radius * np.column_stack((np.cos(theta), np.sin(theta)))
    return np.vstack((front, back, front[:1]))


@dataclass(frozen=True)
class PlaneRaster:
    """Row-major values: values[y, x], with explicit physical pixel centres."""

    values: np.ndarray
    x_mm: np.ndarray
    y_mm: np.ndarray

    @property
    def bounds(self):
        dx, dy = self.x_mm[1] - self.x_mm[0], self.y_mm[1] - self.y_mm[0]
        return (self.x_mm[0] - dx / 2, self.x_mm[-1] + dx / 2,
                self.y_mm[0] - dy / 2, self.y_mm[-1] + dy / 2)

    def world_points(self, axes, coordinate):
        axes, axis = _axes(axes)
        world = np.empty((*self.values.shape, 3), dtype=float)
        world[..., axes[0]] = self.x_mm[None, :]
        world[..., axes[1]] = self.y_mm[:, None]
        world[..., axis] = coordinate
        return world


def _grid(bounds, spacing, max_pixels):
    bounds = np.asarray(bounds, dtype=float)
    spacing = np.asarray(spacing, dtype=float)
    if spacing.ndim == 0:
        spacing = np.repeat(spacing, 2)
    if (bounds.shape != (4,) or not np.isfinite(bounds).all()
            or bounds[1] <= bounds[0] or bounds[3] <= bounds[2]
            or spacing.shape != (2,) or not np.isfinite(spacing).all()
            or np.any(spacing <= 0) or int(max_pixels) != max_pixels or max_pixels < 2):
        raise ValueError("invalid plane bounds, spacing, or max_pixels")
    # Edges encompass the requested bounds exactly even when resolution is capped.
    nx, ny = [
        max(2, int(min(max_pixels, np.ceil((hi - lo) / step))))
        for lo, hi, step in zip(bounds[::2], bounds[1::2], spacing)
    ]
    x = bounds[0] + (np.arange(nx) + .5) * (bounds[1] - bounds[0]) / nx
    y = bounds[2] + (np.arange(ny) + .5) * (bounds[3] - bounds[2]) / ny
    return PlaneRaster(np.zeros((ny, nx), dtype=np.uint8), x, y)


def sample_capsule_slice(entry, tip, radius, axes, coordinate, bounds=None, *,
                         spacing=None, max_pixels=512):
    """Rasterize the exact finite capsule predicate at the current CT plane.

    None means no positive-area cross-section. Optional bounds clip computation,
    not the projected silhouette. spacing is scalar or (horizontal, vertical) mm.
    """
    axes, axis = _axes(axes)
    entry, tip, radius = _point(entry), _point(tip), _radius(radius)
    if not np.isfinite(coordinate):
        raise ValueError("plane coordinate must be finite")
    if (radius == 0 or coordinate <= min(entry[axis], tip[axis]) - radius
            or coordinate >= max(entry[axis], tip[axis]) + radius):
        return None
    low = np.minimum(entry, tip)[list(axes)] - radius
    high = np.maximum(entry, tip)[list(axes)] + radius
    if bounds is not None:
        bounds = np.asarray(bounds, dtype=float)
        if (bounds.shape != (4,) or not np.isfinite(bounds).all()
                or np.any(bounds[1::2] <= bounds[::2])):
            raise ValueError("invalid clipping bounds")
        low, high = np.maximum(low, bounds[::2]), np.minimum(high, bounds[1::2])
    if np.any(high <= low):
        return None
    raster = _grid((low[0], high[0], low[1], high[1]),
                   max(radius / 8, .05) if spacing is None else spacing, max_pixels)
    values = capsule_contains(raster.world_points(axes, coordinate), entry, tip, radius)
    return PlaneRaster(values, raster.x_mm, raster.y_mm)


def is_full_mask_target(target):
    """Only explicit full voxel-centre provenance permits a mask boundary."""
    return isinstance(target, SampledMaskTarget) and target.source == "mask_voxel_centers"


def _voxel_keys(indices):
    # Structured triples avoid overflow from flattening enormous source shapes.
    indices = np.ascontiguousarray(indices, dtype=np.int64)
    return indices.view(np.dtype([("i", np.int64), ("j", np.int64), ("k", np.int64)]))[
        ..., 0
    ]


def sample_target_slice(target, categories, axes, coordinate, spacing, max_pixels=512):
    """Nearest-neighbour sample of a sparse native categorical label volume.

    categories is None (yellow mask) or one category string per target point.
    Full voxel centres are validated against source_shape and affine. Sparse or
    subsampled targets return None; callers should retain their point rendering.
    Memory is O(number of supplied points + max_pixels**2), never O(source_shape).
    spacing accepts scalar, two displayed-axis steps, or three physical-axis steps.
    """
    if not is_full_mask_target(target):
        return None
    axes, axis = _axes(axes)
    if not np.isfinite(coordinate):
        raise ValueError("plane coordinate must be finite")
    spacing = np.asarray(spacing, dtype=float)
    if spacing.shape == (3,):
        spacing = spacing[list(axes)]
    affine = np.asarray(target.affine, dtype=float)
    inverse = np.linalg.inv(affine)
    points = target.array()
    native = points @ inverse[:3, :3].T + inverse[:3, 3]
    indices = np.rint(native).astype(np.int64)
    shape = np.asarray(target.source_shape, dtype=np.int64)
    if (not np.allclose(native, indices, rtol=0, atol=1e-5)
            or np.any(indices < 0) or np.any(indices >= shape)):
        raise ValueError("full-mask points must be declared in-bounds voxel centres")
    if categories is None:
        labels = np.full(len(points), CATEGORY_CODES["target"], dtype=np.uint8)
    else:
        categories = list(categories)
        if len(categories) != len(points) or any(
            category not in CATEGORY_CODES or category == "target" for category in categories
        ):
            raise ValueError("one valid coverage category is required per target voxel")
        labels = np.array([CATEGORY_CODES[c] for c in categories], dtype=np.uint8)
    keys = _voxel_keys(indices)
    order = np.argsort(keys)
    keys, labels = keys[order], labels[order]
    if np.any(keys[1:] == keys[:-1]):
        raise ValueError("full-mask voxel centres must be unique")

    # Transform voxel *edges*, including rotations, reflections and shear.
    corners = np.array(list(product(*zip(indices.min(0) - .5, indices.max(0) + .5))))
    corners = corners @ affine[:3, :3].T + affine[:3, 3]
    lo, hi = corners.min(0), corners.max(0)
    if coordinate < lo[axis] or coordinate > hi[axis]:
        return None
    raster = _grid((lo[axes[0]], hi[axes[0]], lo[axes[1]], hi[axes[1]]),
                   spacing, max_pixels)
    world = raster.world_points(axes, coordinate)
    native = world @ inverse[:3, :3].T + inverse[:3, 3]
    # Round nearest, with half-open native voxel cells [i-.5, i+.5).
    nearest = np.floor(native + .5).astype(np.int64)
    valid = np.all((nearest >= 0) & (nearest < shape), axis=-1)
    positions = np.searchsorted(keys, _voxel_keys(nearest))
    safe_positions = np.minimum(positions, len(keys) - 1)
    valid &= (positions < len(keys)) & (keys[safe_positions] == _voxel_keys(nearest))
    raster.values[valid] = labels[safe_positions[valid]]
    return raster


def _add(plot, item, kind, z=16):
    item.planning_kind = kind
    item.setZValue(z)
    plot.addItem(item)
    return item


def _draw_raster(plot, raster, palette, kind):
    import pyqtgraph as pg
    from PySide6 import QtCore, QtGui

    if raster is None or not np.any(raster.values):
        return []
    rgba = np.zeros((*raster.values.shape, 4), dtype=np.uint8)
    for code, color in palette.items():
        rgba[raster.values == code] = color
    image = pg.ImageItem(rgba, axisOrder="row-major")
    image.setAutoDownsample(False)  # Never average categorical labels.
    x0, x1, y0, y1 = raster.bounds
    image.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
    items = [_add(plot, image, kind)]
    dx, dy = (x1 - x0) / rgba.shape[1], (y1 - y0) / rgba.shape[0]
    for code, color in palette.items():
        mask = raster.values == code
        if not mask.any():
            continue
        # Zero padding closes contours even when a region touches a raster edge.
        outline = pg.IsocurveItem(np.pad(mask.astype(float), 1), level=.5,
                                 pen=pg.mkPen((*color[:3], 230), width=1.3),
                                 axisOrder="row-major")
        transform = QtGui.QTransform()
        transform.translate(x0 - dx, y0 - dy)
        transform.scale(dx, dy)
        outline.setTransform(transform)
        items.append(_add(plot, outline, kind + "_outline", 17))
    return items


def draw_portal(plot, center, normal, radius, axes, *, color=(255, 200, 90),
                label="Portal"):
    """Add projected oblique portal rim and explicit projection label; return both."""
    import pyqtgraph as pg

    rim = projected_portal(center, normal, radius, axes)
    outline = pg.PlotDataItem(rim[:, 0], rim[:, 1], pen=pg.mkPen(color, width=2))
    text = pg.TextItem(f"{label} (projected)", color=color, anchor=(0, 1))
    text.setPos(*np.asarray(center)[list(axes)])
    return [_add(plot, outline, "portal_projection", 18),
            _add(plot, text, "portal_projection_label", 24)]


def draw_instrument(plot, entry, tip, radius, axes, coordinate, bounds=None, *,
                    color=INSTRUMENT_COLOR, spacing=None, max_pixels=512):
    """Add CT-plane capsule fill/outline and dashed, labelled shaft projection.

    Returns a flat list of all items added, including the label. An out-of-plane
    instrument has only its dashed projection and label, never solid slice fill.
    """
    import pyqtgraph as pg
    from PySide6 import QtCore

    raster = sample_capsule_slice(entry, tip, radius, axes, coordinate, bounds,
                                  spacing=spacing, max_pixels=max_pixels)
    items = _draw_raster(plot, raster, {1: (*color[:3], 85)}, "instrument_slice")
    rim = projected_capsule(entry, tip, radius, axes)
    outline = pg.PlotDataItem(
        rim[:, 0], rim[:, 1],
        pen=pg.mkPen((*color[:3], 185), width=1.3, style=QtCore.Qt.PenStyle.DashLine),
    )
    text = pg.TextItem("Shaft (projected)", color=color, anchor=(0, 1))
    text.setPos(*np.asarray(entry)[list(axes)])
    items.extend([_add(plot, outline, "instrument_projection", 21),
                  _add(plot, text, "instrument_projection_label", 24)])
    return items


def draw_target_slice(plot, target, categories, axes, coordinate, spacing, max_pixels=512):
    """Add nearest-neighbour RGBA categorical mask and each category's contour.

    Returns [] for sparse/subsampled targets (caller retains dots) and for planes
    with no mask pixels. This helper makes no reachability or feasibility inference.
    """
    raster = sample_target_slice(target, categories, axes, coordinate, spacing, max_pixels)
    palette = {CATEGORY_CODES[name]: color for name, color in CATEGORY_COLORS.items()}
    return _draw_raster(plot, raster, palette, "target_slice")
