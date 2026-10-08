"""Slice intersections of the union of explicit sampled swept capsules.

No convex hull is taken: gaps and obstacles between sampled paths stay gaps.
This is a geometric occupancy display, not an independent feasibility test.
"""
import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore


def capsule_union_slice(paths, radius, axes, coordinate, spacing=0.5, max_pixels=256):
    if not paths or radius <= 0:
        return None
    axis = ({0, 1, 2} - set(axes)).pop()
    endpoints = np.array([[p.entry_mm, p.tip_mm] for p in paths])
    lo = endpoints.min(axis=(0, 1)) - radius - spacing
    hi = endpoints.max(axis=(0, 1)) + radius + spacing
    if not lo[axis] <= coordinate <= hi[axis]:
        return None
    counts = np.minimum(max_pixels, np.maximum(2, np.ceil((hi - lo) / spacing).astype(int)))
    x, y = axes
    dx, dy = (hi[x] - lo[x]) / counts[x], (hi[y] - lo[y]) / counts[y]
    xx, yy = np.meshgrid(lo[x] + (np.arange(counts[x]) + .5) * dx,
                         lo[y] + (np.arange(counts[y]) + .5) * dy)
    points = np.zeros((*xx.shape, 3))
    points[..., x], points[..., y], points[..., axis] = xx, yy, coordinate
    occupied = np.zeros(xx.shape, dtype=bool)
    for entry, tip in endpoints:
        delta = tip - entry
        norm2 = float(delta @ delta)
        t = np.zeros(xx.shape) if norm2 == 0 else np.clip(
            np.sum((points - entry) * delta, axis=-1) / norm2, 0, 1)
        occupied |= np.sum((points - entry - t[..., None] * delta) ** 2, axis=-1) <= radius**2
    return occupied, (lo[x], lo[y], hi[x] - lo[x], hi[y] - lo[y])


def draw_sampled_corridor(plot, paths, radius, axes, coordinate, color):
    sample = capsule_union_slice(paths, radius, axes, coordinate)
    if sample is None:
        return []
    occupied, bounds = sample
    rgba = np.zeros((*occupied.shape, 4), np.uint8)
    rgba[occupied] = (*color[:3], 35)
    interior = occupied.copy()
    interior[1:, :] &= occupied[:-1, :]
    interior[:-1, :] &= occupied[1:, :]
    interior[:, 1:] &= occupied[:, :-1]
    interior[:, :-1] &= occupied[:, 1:]
    interior[[0, -1], :] = False
    interior[:, [0, -1]] = False
    rgba[occupied & ~interior] = (*color[:3], 160)
    image = pg.ImageItem(rgba, axisOrder="row-major")
    image.setRect(QtCore.QRectF(*bounds))
    image.setZValue(8)
    plot.addItem(image)
    return [image]
