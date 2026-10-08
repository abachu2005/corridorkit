"""Anatomical RAS-mm PACS workspace, with optional desktop-only VTK rendering.

Parent integration: ``set_case(case, visible_approaches=None)``, ``load_volume(
path, series_uid=None, assume_spatial_unit=None)``, ``set_result(result|None)``,
``clear_volume()``, ``set_mask(layer, Volume|None)``. Loading returns the native
Volume; ``source_volume`` retains native geometry, ``display_volume`` is the
orthogonal RAS grid, and ``volume`` remains its xyz ndarray for compatibility.

Signals: ``point_picked(tuple)`` emits RAS mm in any pick mode;
``target_picked(tuple)`` / ``portal_picked(tuple)`` emit for ``set_pick_mode``.
``mask_changed(str, object)`` emits (layer, native Volume or None).
``crosshair_changed(tuple)`` emits linked RAS-mm positions.
Layer editing: ``edit_mask(layer, center_mm, radius_mm, label, **brush_options)``,
``undo_mask(layer)``, ``redo_mask(layer)``. App owns anatomical approval/save.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from corridorkit.desktop.editing import MaskEditor
from corridorkit.desktop.trajectory_geometry import (
    clip_segment_to_slab,
    feasible_paths,
    project_segment,
    resample_path_plane,
)
from corridorkit.domain.models import CorridorCase
from corridorkit.io.volumes import (
    Volume,
    orthogonal_resample,
    resample_to_grid,
    validate_alignment,
    validate_labels,
)
from corridorkit.io.volumes import (
    load_volume as read_volume,
)

APPROACH_COLORS = {"eea": (58, 166, 255), "transmaxillary": (240, 154, 62)}
TARGET_COLOR = (203, 163, 255)
PROTECTED_COLOR = (235, 92, 92)
WITNESS_COLOR = (66, 225, 164)
LABEL_COLORS = ((239, 100, 100), (88, 184, 255), (210, 152, 255), (244, 189, 82), (91, 220, 180))


def slice_array(data, axis, value):
    return np.take(data, value, axis=axis).T


def in_slice(points, axis, coordinate, thickness):
    points = np.asarray(points, float).reshape(-1, 3)
    return np.abs(points[:, axis] - coordinate) <= thickness / 2 + 1e-8


class ProjectionView(QtWidgets.QWidget):
    """True orthogonal slice (historical name retained for callers)."""

    slice_changed = QtCore.Signal(int)
    clicked = QtCore.Signal(float, float)

    def __init__(self, title: str, axes: tuple[int, int]):
        super().__init__()
        self.axes = axes
        self.axis = next(i for i in range(3) if i not in axes)
        self.title = title
        self.overlays = []
        self.trajectory_items = {}
        self.mask_items = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        self.heading = QtWidgets.QLabel(title)
        self.heading.setObjectName("planeHeading")
        layout.addWidget(self.heading)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#090e15")
        self.plot.setAspectLocked(True)
        self.plot.setLabel("bottom", ("R", "A", "S")[axes[0]], units="mm")
        self.plot.setLabel("left", ("R", "A", "S")[axes[1]], units="mm")
        # Radiological axial/coronal: patient's right is screen left.
        self.plot.invertX(axes[0] == 0)
        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.plot.addItem(self.image_item)
        self.crosshairs = [
            pg.InfiniteLine(angle=90, pen=pg.mkPen("#42c9ca", width=1)),
            pg.InfiniteLine(angle=0, pen=pg.mkPen("#42c9ca", width=1)),
        ]
        for line in self.crosshairs:
            line.setZValue(20)
            self.plot.addItem(line)
        self.plot.scene().sigMouseClicked.connect(self._clicked)
        layout.addWidget(self.plot, 1)
        orientation = {
            2: "R  ←   axial   →  L     •     A ↑ / P ↓",
            1: "R  ←   coronal   →  L     •     S ↑ / I ↓",
            0: "P  ←   sagittal   →  A     •     S ↑ / I ↓",
        }
        self.coordinates = QtWidgets.QLabel(orientation[self.axis])
        self.orientation_text = orientation[self.axis]
        layout.addWidget(self.coordinates)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self.slice_changed)
        layout.addWidget(self.slider)
        self.clear()

    def _clicked(self, event):
        if (
            event.button() == QtCore.Qt.MouseButton.LeftButton
            and self.plot.getViewBox().sceneBoundingRect().contains(event.scenePos())
        ):
            point = self.plot.getViewBox().mapSceneToView(event.scenePos())
            self.clicked.emit(point.x(), point.y())

    def clear_overlays(self):
        for item in self.overlays:
            self.plot.removeItem(item)
        self.overlays.clear()
        self.trajectory_items.clear()

    def draw_trajectory(self, path, color, coordinate, thickness, radius_mm=0.0):
        """Dashed whole-path projection; solid only where inside this slice slab."""
        projected = project_segment(path.entry_mm, path.tip_mm, self.axes)
        if radius_mm > 0:
            # Orthographic projection of the finite swept capsule. This is NOT
            # a slice cross-section; retain the dashed projection convention.
            angles = np.linspace(0, 2 * np.pi, 33)
            for endpoint_index, endpoint in enumerate(projected):
                outline = self.plot.plot(
                    endpoint[0] + radius_mm * np.cos(angles),
                    endpoint[1] + radius_mm * np.sin(angles),
                    pen=pg.mkPen((*color[:3], 110), width=1,
                                 style=QtCore.Qt.PenStyle.DashLine),
                )
                self.trajectory_items[f"shaft_cap_{endpoint_index}"] = outline
            delta = projected[1] - projected[0]
            norm = np.linalg.norm(delta)
            if norm > 1e-9:
                perpendicular = np.array([-delta[1], delta[0]]) / norm
                for sign in (-1, 1):
                    edge = projected + sign * radius_mm * perpendicular
                    self.trajectory_items[f"shaft_edge_{sign}"] = self.plot.plot(
                        edge[:, 0], edge[:, 1],
                        pen=pg.mkPen((*color[:3], 110), width=1,
                                     style=QtCore.Qt.PenStyle.DashLine),
                    )
        item = self.plot.plot(
            projected[:, 0], projected[:, 1],
            pen=pg.mkPen(color, width=2, style=QtCore.Qt.PenStyle.DashLine),
        )
        item.setZValue(21)
        self.trajectory_items["projection"] = item
        clipped = clip_segment_to_slab(
            path.entry_mm, path.tip_mm, self.axis, coordinate, thickness,
        )
        if clipped is not None:
            points = clipped[:, list(self.axes)]
            item = self.plot.plot(
                points[:, 0], points[:, 1], pen=pg.mkPen(color, width=4),
                symbol="o" if np.allclose(points[0], points[1]) else None,
                symbolSize=7, symbolBrush=color,
            )
            item.setZValue(22)
            self.trajectory_items["intersection"] = item
        for label, point, symbol in (
            ("entry", path.entry_mm, "s"), ("tip", path.tip_mm, "t"),
        ):
            within = bool(in_slice([point], self.axis, coordinate, thickness)[0])
            x, y = point[list(self.axes)]
            marker = self.plot.plot(
                [x], [y], pen=None, symbol=symbol, symbolSize=11,
                symbolPen=pg.mkPen(color, width=2),
                symbolBrush=pg.mkBrush(color if within else (0, 0, 0, 0)),
            )
            marker.setZValue(23)
            self.trajectory_items[label] = marker
            text = pg.TextItem(
                f"{label.title()} {'in slab' if within else 'projected'}",
                color=color, anchor=(0, 1),
            )
            text.setPos(x, y)
            text.setZValue(24)
            # The portal carries the entry label; retain marker handles without
            # stacking a second entry caption at the same physical location.
            text.setVisible(False)
            self.plot.addItem(text)
            self.trajectory_items[f"{label}_label"] = text
        self.overlays.extend(self.trajectory_items.values())

    def clear(self):
        self.image_item.clear()
        self.clear_overlays()
        for item in self.mask_items.values():
            self.plot.removeItem(item)
        self.mask_items.clear()
        self.slider.setEnabled(False)
        self.slider.setRange(0, 0)
        self.heading.setText(f"{self.title} • No source image")
        self.coordinates.setText(self.orientation_text)
        for line in self.crosshairs:
            line.hide()

    def plot_points(self, points, color, symbol="o"):
        if len(points):
            item = self.plot.plot(
                points[:, self.axes[0]],
                points[:, self.axes[1]],
                pen=None,
                symbol=symbol,
                symbolSize=8,
                symbolBrush=color,
            )
            item.setZValue(15)
            self.overlays.append(item)

    def ellipse(self, center, radius, color):
        item = QtWidgets.QGraphicsEllipseItem(
            center[self.axes[0]] - radius, center[self.axes[1]] - radius, radius * 2, radius * 2
        )
        item.setPen(pg.mkPen(color, width=1.5))
        item.setBrush(pg.mkBrush((*color, 30)))
        item.setZValue(10)
        self.plot.addItem(item)
        self.overlays.append(item)

    def set_slice(self, image, bounds=None, levels=None):
        if image is None:
            self.image_item.clear()
            return
        self.image_item.setImage(image, levels=levels or (0, 1), autoLevels=False)
        if bounds:
            x0, x1, y0, y1 = bounds
            self.image_item.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))


class PathView(QtWidgets.QWidget):
    """Native-affine CT plane containing the selected entry-to-tip witness."""

    def __init__(self):
        super().__init__()
        self.sample = None
        self.trajectory_items = {}
        layout = QtWidgets.QVBoxLayout(self)
        self.heading = QtWidgets.QLabel()
        self.heading.setWordWrap(True)
        layout.addWidget(self.heading)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("#090e15")
        self.plot.setAspectLocked(True)
        self.plot.setLabel("bottom", "Along path from entry", units="mm")
        self.plot.setLabel("left", "Across path", units="mm")
        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.plot.addItem(self.image_item)
        layout.addWidget(self.plot, 1)
        self.caption = QtWidgets.QLabel(
            "Native CT interpolation • dark background = outside source FOV\n"
            "Solid line lies in this plane; square = entry, triangle = tip."
        )
        self.caption.setWordWrap(True)
        layout.addWidget(self.caption)
        self.clear()

    def clear(self, message="No selected feasible witness • analysis unavailable"):
        self.sample = None
        self.image_item.clear()
        for item in self.trajectory_items.values():
            self.plot.removeItem(item)
        self.trajectory_items.clear()
        self.heading.setText(message)

    def set_path(self, volume, path, color, levels, radius_mm=0.0, tip_radius_mm=0.0,
                 assessed=True):
        self.clear()
        if path is None:
            return
        if volume is None:
            self.heading.setText("Selected witness available • load source CT for path view")
            return
        self.sample = resample_path_plane(volume, path.entry_mm, path.tip_mm)
        x0, x1, y0, y1 = self.sample.bounds
        self.image_item.setImage(self.sample.image, levels=levels, autoLevels=False)
        self.image_item.setRect(QtCore.QRectF(x0, y0, x1 - x0, y1 - y0))
        self.trajectory_items["path"] = self.plot.plot(
            [0, path.length_mm], [0, 0], pen=pg.mkPen(color, width=3),
        )
        # Dimensioned shaft cross-section in the path plane, not a cosmetic
        # pixel-width line. Rounded caps match the engine's swept capsule.
        outline = QtGui.QPainterPath()
        outline.addRoundedRect(
            QtCore.QRectF(-radius_mm, -radius_mm,
                         path.length_mm + 2 * radius_mm, 2 * radius_mm),
            radius_mm, radius_mm,
        )
        shaft = QtWidgets.QGraphicsPathItem(outline)
        shaft.setPen(pg.mkPen(color, width=1.5))
        shaft.setBrush(pg.mkBrush((*color[:3], 100)))
        self.plot.addItem(shaft)
        self.trajectory_items["dimensioned_shaft"] = shaft
        if tip_radius_mm > 0:
            tip = QtWidgets.QGraphicsEllipseItem(
                path.length_mm - tip_radius_mm, -tip_radius_mm,
                2 * tip_radius_mm, 2 * tip_radius_mm)
            tip.setPen(pg.mkPen(color, style=QtCore.Qt.PenStyle.DotLine))
            tip.setBrush(pg.mkBrush((*color[:3], 35)))
            self.plot.addItem(tip)
            self.trajectory_items["tip_working_radius"] = tip
        for name, x, symbol in (("Entry", 0, "s"), ("Tip", path.length_mm, "t")):
            self.trajectory_items[name] = self.plot.plot(
                [x], [0], pen=None, symbol=symbol, symbolSize=12, symbolBrush=color,
            )
            text = pg.TextItem(name, color=color, anchor=(0.5, 0))
            text.setPos(x, -max(radius_mm + 4, 5))
            self.plot.addItem(text)
            self.trajectory_items[f"{name}_label"] = text
        for item in self.trajectory_items.values():
            item.setZValue(20)
        vector = lambda values: ", ".join(f"{v:.2f}" for v in values)
        details = (
            f"{path.approach_name} • "
            f"{'feasible sampled witness #' + str(path.trajectory_index + 1) if assessed else 'INTENDED — NOT ASSESSED'}"
            f" • {path.length_mm:.1f} mm\n"
            f"Entry RAS ({vector(path.entry_mm)}) → tip ({vector(path.tip_mm)}) mm\n"
            f"Across-plane RAS direction ({vector(self.sample.across_direction)})"
            f"\nShaft Ø {2 * radius_mm:.1f} mm · tip working radius {tip_radius_mm:.1f} mm"
        )
        self.heading.setToolTip(details)
        self.heading.setText(
            f"{'Computed path' if assessed else 'Intended path · NOT ASSESSED'}\n"
            f"{path.length_mm:.1f} mm insertion   ·   Ø {2 * radius_mm:.1f} mm shaft")
        self.plot.setRange(xRange=(x0, x1), yRange=(y0, y1), padding=0)


class ThreeDView(QtWidgets.QWidget):
    """Actual affine-transformed image surfaces plus finite geometric witnesses."""

    def __init__(self):
        super().__init__()
        self.available = False
        self._vtk_widget = None
        self.case = self.result = self.volume = None
        self.selected_trajectory = None
        self.coverage_labels = None
        self.masks = {}
        self.visible = set()
        self.opacity = 0.45
        self.bone_threshold = 300.0
        self.clip_plane = None
        self._surface_cache = {}
        layout = QtWidgets.QVBoxLayout(self)
        self.heading = QtWidgets.QLabel(
            "3D target / instrument • physical dimensions\n"
            "Selected shaft in approach color; other witnesses green"
        )
        layout.addWidget(self.heading)
        platform = QtWidgets.QApplication.platformName().lower()
        if platform in {"offscreen", "minimal"} or os.environ.get(
            "QT_QPA_PLATFORM", ""
        ).lower() in {"offscreen", "minimal"}:
            message = "3D disabled on headless Qt; no VTK context created."
        else:
            try:
                import vtk

                from corridorkit.desktop.native_vtk import (
                    PacedVTKInteractor as QVTKRenderWindowInteractor,
                )

                self._vtk = vtk
                self._vtk_widget = QVTKRenderWindowInteractor(self)
                self.renderer = vtk.vtkRenderer()
                self.renderer.SetBackground(0.035, 0.05, 0.07)
                self._vtk_widget.GetRenderWindow().AddRenderer(self.renderer)
                layout.addWidget(self._vtk_widget, 1)
                self._vtk_widget.Initialize()
                self.available = True
                return
            except ImportError:
                message = "3D unavailable: VTK desktop support is not installed."
        label = QtWidgets.QLabel(message)
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label, 1)

    def _actor(self, source, color, opacity=1.0):
        mapper = self._vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(source.GetOutputPort())
        mapper.ScalarVisibilityOff()
        if self.clip_plane is not None:
            mapper.AddClippingPlane(self.clip_plane)
        actor = self._vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*(x / 255 for x in color))
        actor.GetProperty().SetOpacity(opacity)
        self.renderer.AddActor(actor)
        return actor

    def _sphere(self, point, radius, color, opacity=1.0):
        if radius <= 0:
            return
        source = self._vtk.vtkSphereSource()
        source.SetCenter(*point)
        source.SetRadius(radius)
        source.SetThetaResolution(20)
        source.SetPhiResolution(16)
        self._actor(source, color, opacity)

    def _shaft(self, start, direction, length, radius, color, opacity):
        end = np.asarray(start) + np.asarray(direction) * length
        line = self._vtk.vtkLineSource()
        line.SetPoint1(*start)
        line.SetPoint2(*end)
        if radius > 0:
            tube = self._vtk.vtkTubeFilter()
            tube.SetInputConnection(line.GetOutputPort())
            tube.SetRadius(radius)
            tube.SetNumberOfSides(24)
            tube.CappingOn()
            self._actor(tube, color, opacity)
            # Engine's finite shaft collision model is a capsule.
            self._sphere(start, radius, color, opacity)
            self._sphere(end, radius, color, opacity)
        else:
            self._actor(line, color, opacity)

    def _surface(self, volume, level, color, *, label=False):
        from vtk.util.numpy_support import numpy_to_vtk

        key = (id(volume), level, label)
        if key not in self._surface_cache:
            image = self._vtk.vtkImageData()
            data = (
                (volume.data == level).astype(np.float32)
                if label
                else np.nan_to_num(volume.data.astype(np.float32), nan=-1e6)
            )
            if label:
                # Close labels at the native FOV boundary without shifting anatomy.
                data = np.pad(data, 1, constant_values=0)
                image.SetOrigin(-1, -1, -1)
            image.SetDimensions(*data.shape)
            image.GetPointData().SetScalars(numpy_to_vtk(data.ravel(order="F"), deep=True))
            contour = self._vtk.vtkFlyingEdges3D()
            contour.SetInputData(image)
            contour.SetValue(0, 0.5 if label else level)
            matrix = self._vtk.vtkMatrix4x4()
            for i in range(4):
                for j in range(4):
                    matrix.SetElement(i, j, volume.affine[i, j])
            transform = self._vtk.vtkTransform()
            transform.SetMatrix(matrix)
            surface = self._vtk.vtkTransformPolyDataFilter()
            surface.SetInputConnection(contour.GetOutputPort())
            surface.SetTransform(transform)
            surface.Update()
            self._surface_cache[key] = surface
        self._actor(self._surface_cache[key], color, self.opacity)

    def set_case(self, case, visible_approaches=None):
        self.case = case
        self.visible = (
            {a.name for a in case.approaches}
            if visible_approaches is None
            else set(visible_approaches)
        )
        self.refresh()

    def refresh(self, reset_camera=False):
        if not self.available:
            return
        self.renderer.RemoveAllViewProps()
        if self.volume is not None and self.volume.intensity_unit == "HU":
            self._surface(self.volume, self.bone_threshold, (216, 208, 188))
        for volume in self.masks.values():
            for label in np.unique(volume.data):
                if label:
                    self._surface(
                        volume,
                        int(label),
                        LABEL_COLORS[(int(label) - 1) % len(LABEL_COLORS)],
                        label=True,
                    )
        if self.case is not None:
            palette = {
                "eea_only": APPROACH_COLORS["eea"],
                "tm_only": APPROACH_COLORS["transmaxillary"],
                "both": (180, 100, 230), "unreached": (145, 150, 160),
                "unavailable": (235, 220, 155),
            }
            target = self.case.target
            full_mask = getattr(target, "source", "") == "mask_voxel_centers"
            if full_mask:
                # Crop to occupied target cells, preserving its native affine.
                points = target.array()
                affine = np.asarray(target.affine)
                indices = np.rint(np.linalg.solve(
                    affine[:3, :3], (points - affine[:3, 3]).T).T).astype(int)
                low, high = indices.min(axis=0), indices.max(axis=0)
                shape = high - low + 1
                if np.prod(shape, dtype=np.int64) <= 8_000_000:
                    labels = np.ones(len(points), dtype=np.uint8)
                    keys = list(palette)
                    if self.coverage_labels is not None:
                        labels = np.array([keys.index(v) + 1 for v in self.coverage_labels], np.uint8)
                    data = np.zeros(tuple(shape), np.uint8)
                    data[tuple((indices - low).T)] = labels
                    cropped = affine.copy()
                    cropped[:3, 3] = affine[:3, :3] @ low + affine[:3, 3]
                    # Avoid stale cached geometry when coverage categories change.
                    cache_key = (id(target), tuple(labels.tolist()))
                    if getattr(self, "_target_surface_key", None) != cache_key:
                        self._surface_cache.clear()
                        self._target_surface_key = cache_key
                        self._target_surface_volume = Volume(data, cropped, "label")
                    for value in np.unique(labels):
                        color = TARGET_COLOR if self.coverage_labels is None else palette[keys[value - 1]]
                        self._surface(self._target_surface_volume, int(value), color, label=True)
                else:
                    full_mask = False
            if not full_mask:
                for index, point in enumerate(target.array()):
                    color = (TARGET_COLOR if self.coverage_labels is None
                             else palette[self.coverage_labels[index]])
                    self._sphere(point, 0.8, color)
            for structure in self.case.protected_structures:
                geometry = structure.geometry
                if geometry is not None and geometry.kind == "sphere":
                    self._sphere(geometry.center_mm, geometry.radius_mm, PROTECTED_COLOR, 0.3)
            for approach in self.case.approaches:
                if approach.name not in self.visible:
                    continue
                color = APPROACH_COLORS[approach.kind.value]
                portal = self._vtk.vtkRegularPolygonSource()
                portal.SetCenter(*approach.portal.center_mm)
                portal.SetNormal(*approach.portal.normal)
                portal.SetRadius(approach.portal.radius_mm)
                portal.SetNumberOfSides(64)
                self._actor(portal, color, 0.3)
                # Do not present nominal, untested directions as instrument paths.
            if self.result is not None:
                configs = {a.name: a for a in self.case.approaches}
                for result in self.result.approaches:
                    if (result.name not in self.visible or result.witness_direction is None
                            or getattr(result, "status", "complete") != "complete"):
                        continue
                    approach = configs[result.name]
                    depth = result.best_working_depth_mm
                    entry = getattr(result, "witness_entry_point_mm", None)
                    if entry is not None and depth is not None:
                        self._shaft(
                            entry,
                            result.witness_direction,
                            min(depth, approach.instrument.length_mm),
                            approach.instrument.radius_mm,
                            WITNESS_COLOR,
                            0.9,
                        )
                        self._sphere(
                            np.asarray(entry) + np.asarray(result.witness_direction) * depth,
                            approach.instrument.tip_working_radius_mm,
                            WITNESS_COLOR,
                            0.3,
                        )
                    if self.coverage_labels is None:
                        for index in result.reached_point_indices:
                            self._sphere(self.case.target.array()[index], 1.1, WITNESS_COLOR)
                for pair in self.result.simultaneous_pairs:
                    if not pair.feasible or getattr(pair, "status", "complete") != "complete":
                        continue
                    for name, direction, entry, reached in (
                        (
                            pair.first,
                            pair.first_direction,
                            getattr(pair, "first_entry_point_mm", None),
                            pair.first_reached_point_indices,
                        ),
                        (
                            pair.second,
                            pair.second_direction,
                            getattr(pair, "second_entry_point_mm", None),
                            pair.second_reached_point_indices,
                        ),
                    ):
                        if (name in self.visible and direction is not None and reached
                                and entry is not None):
                            approach = configs[name]
                            start = np.asarray(entry)
                            depth = np.max((self.case.target.array()[reached] - start) @ direction)
                            self._shaft(
                                start,
                                direction,
                                min(float(depth), approach.instrument.length_mm),
                                approach.instrument.radius_mm,
                                (255, 232, 120),
                                0.5,
                            )
        selected = self.selected_trajectory
        if selected is not None and selected.approach_name in self.visible:
            config = next(a for a in self.case.approaches if a.name == selected.approach_name)
            color = APPROACH_COLORS[config.kind.value]
            self._shaft(
                selected.entry_mm, (selected.tip_mm - selected.entry_mm) / selected.length_mm,
                selected.length_mm, config.instrument.radius_mm, color, 1.0,
            )
            self._sphere(selected.entry_mm, 1.5, color)
            self._sphere(selected.tip_mm, 1.5, color)
        if reset_camera:
            self.renderer.ResetCamera()
        self._vtk_widget.GetRenderWindow().Render()

    def closeEvent(self, event):
        if self._vtk_widget is not None:
            self._vtk_widget.Finalize()
        super().closeEvent(event)


class LinkedViewer(QtWidgets.QWidget):
    point_picked = QtCore.Signal(tuple)
    target_picked = QtCore.Signal(tuple)
    portal_picked = QtCore.Signal(tuple)
    crosshair_changed = QtCore.Signal(tuple)
    mask_changed = QtCore.Signal(str, object)

    def __init__(self):
        super().__init__()
        self.case = self.result = None
        self.intended_paths = []
        self.selected_trajectory = None
        self.coverage_labels = None
        self.source_volume = self.display_volume = self.volume = None
        self.masks = {}
        self.display_masks = {}
        self.mri_overlay = None
        self.editors = {}
        self.crosshair = np.zeros(3)
        self.visible = set()
        self.pick_mode = "crosshair"
        self.setStyleSheet(
            "QWidget {background:#111923;color:#dbe5ef;} "
            "QLabel#planeHeading {color:#79c7e6;font-weight:600;padding:4px;} "
            "QDoubleSpinBox,QComboBox {background:#202e3c;padding:3px;} "
            "QPushButton {background:#24364a;padding:5px;border:1px solid #36516a;}"
        )
        outer = QtWidgets.QVBoxLayout(self)
        toolbar = QtWidgets.QHBoxLayout()
        self.source_label = QtWidgets.QLabel("No source image • RAS mm")
        self.source_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                        QtWidgets.QSizePolicy.Policy.Preferred)
        toolbar.addWidget(self.source_label, 1)
        self.window = QtWidgets.QDoubleSpinBox()
        self.window.setRange(0.001, 1e9)
        self.window.setValue(1000)
        self.level = QtWidgets.QDoubleSpinBox()
        self.level.setRange(-1e9, 1e9)
        self.level.setValue(300)
        for title, widget in (("W", self.window), ("L", self.level)):
            toolbar.addWidget(QtWidgets.QLabel(title))
            toolbar.addWidget(widget)
            widget.valueChanged.connect(self._render_all)
        self.opacity = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(45)
        self.opacity.setMaximumWidth(90)
        self.opacity.valueChanged.connect(self._opacity_changed)
        toolbar.addWidget(self.opacity)
        self.pick_control = QtWidgets.QComboBox()
        self.pick_control.addItems(["crosshair", "target", "portal"])
        self.pick_control.currentTextChanged.connect(self.set_pick_mode)
        toolbar.addWidget(self.pick_control)
        self.pick_control.hide()  # One picking tool, owned by the planning workflow.
        self.confirm_hu = QtWidgets.QCheckBox("CT values are HU")
        self.confirm_hu.toggled.connect(self._confirm_hu)
        toolbar.addWidget(self.confirm_hu)
        reset = QtWidgets.QPushButton("Reset")
        reset.clicked.connect(self.reset_view)
        toolbar.addWidget(reset)
        outer.addLayout(toolbar)
        self.display_settings = QtWidgets.QWidget()
        clipbar = QtWidgets.QHBoxLayout(self.display_settings)
        self.clip_control = QtWidgets.QComboBox()
        self.clip_control.addItems(["No 3D clipping", "Clip R", "Clip A", "Clip S"])
        self.clip_control.currentIndexChanged.connect(self._clip_changed)
        clipbar.addWidget(self.clip_control)
        clipbar.addWidget(QtWidgets.QLabel("Opacity"))
        outer.addWidget(self.display_settings)
        self.display_settings.hide()
        display_bar = QtWidgets.QHBoxLayout()
        self.approach_display = QtWidgets.QComboBox()
        self.approach_display.addItems(["Combined comparison", "EEA only", "Transmaxillary only"])
        self.approach_display.currentIndexChanged.connect(self._display_approach)
        display_bar.addWidget(self.approach_display)
        self.corridor_toggle = QtWidgets.QCheckBox("Sampled shaft occupancy")
        self.corridor_toggle.setToolTip(
            "Union of explicit feasible swept shafts in this slice; not a continuous safe corridor.")
        self.corridor_toggle.toggled.connect(self._render_all)
        display_bar.addWidget(self.corridor_toggle)
        display_bar.addStretch()
        display_options = QtWidgets.QPushButton("Display settings")
        display_options.setCheckable(True)
        display_options.toggled.connect(self.display_settings.setVisible)
        display_bar.addWidget(display_options)
        for widget in (self.confirm_hu, self.opacity):
            toolbar.removeWidget(widget)
            clipbar.addWidget(widget)
        outer.addLayout(display_bar)
        self.views = [
            ProjectionView("Axial", (0, 1)),
            ProjectionView("Coronal", (0, 2)),
            ProjectionView("Sagittal", (1, 2)),
        ]
        self.three_d = ThreeDView()
        self.path_view = PathView()
        self.detail_tabs = QtWidgets.QTabWidget()
        self.detail_tabs.addTab(self.three_d, "3D anatomy")
        self.detail_tabs.addTab(self.path_view, "Path-aligned CT")
        self.support_tabs = QtWidgets.QTabWidget()
        self.support_tabs.addTab(self.views[1], "Coronal")
        self.support_tabs.addTab(self.views[2], "Sagittal")
        self.support_tabs.addTab(self.detail_tabs, "Path / 3D")
        for index, view in enumerate(self.views):
            view.slice_changed.connect(lambda value, p=index: self._render_slice(p, value))
            view.clicked.connect(lambda x, y, p=index: self.pick_in_view(p, x, y))
        self.planning_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.planning_splitter.addWidget(self.views[0])
        self.planning_splitter.addWidget(self.support_tabs)
        self.planning_splitter.setStretchFactor(0, 3)
        self.planning_splitter.setStretchFactor(1, 1)
        self.planning_splitter.setSizes([1050, 350])
        outer.addWidget(self.planning_splitter, 1)
        self.trajectory_label = QtWidgets.QLabel(
            "No selected feasible witness • analysis unavailable"
        )
        self.trajectory_label.setWordWrap(True)
        self.trajectory_label.hide()

    def clear_volume(self):
        self.source_volume = self.display_volume = self.volume = None
        self.masks.clear()
        self.display_masks.clear()
        self.mri_overlay = None
        self.editors.clear()
        self.result = self.three_d.result = None
        self._clear_trajectory()
        self.coverage_labels = None
        self.three_d.coverage_labels = None
        self.three_d.volume = None
        self.three_d.masks = {}
        self.three_d._surface_cache.clear()
        self.source_label.setText("No source image • RAS mm")
        for view in self.views:
            view.clear()
        self.three_d.refresh()

    def set_intended_paths(self, paths):
        self.intended_paths = list(paths)
        self._render_all()

    def inspect_intended_path(self, planned):
        from corridorkit.desktop.trajectory_geometry import SelectedTrajectory
        config = next(a for a in self.case.approaches if a.name == planned.approach_name)
        path = SelectedTrajectory(planned.approach_name, -1, np.array(planned.entry_mm),
                                  np.array(planned.tip_mm), ())
        self.path_view.set_path(
            self.source_volume, path, (235, 220, 155),
            (self.level.value() - self.window.value()/2, self.level.value() + self.window.value()/2),
            config.instrument.radius_mm, config.instrument.tip_working_radius_mm, assessed=False)
        self.support_tabs.setCurrentWidget(self.detail_tabs)
        self.detail_tabs.setCurrentWidget(self.path_view)

    def _display_approach(self):
        if self.case is None:
            return
        mode = self.approach_display.currentIndex()
        kind = {1: "eea", 2: "transmaxillary"}.get(mode)
        self.visible = {a.name for a in self.case.approaches if kind is None or a.kind.value == kind}
        self.three_d.visible = self.visible
        candidates = [p for name in sorted(self.visible) for p in self.available_trajectories(name)]
        target_index = getattr(self, "inspected_target_index", None)
        if target_index is not None:
            candidates = [p for p in candidates if target_index in p.reached_point_indices]
        if candidates:
            best = max(candidates, key=self._trajectory_rank)
            self.select_trajectory(best.approach_name, best.trajectory_index)
        else:
            self._clear_trajectory("No feasible witness for displayed approach")
            self.three_d.refresh()
            self._render_all()

    def _confirm_hu(self, confirmed):
        if self.source_volume is None:
            return
        self.source_volume.intensity_unit = "HU" if confirmed else "unknown"
        self.three_d.refresh()

    def _clip_changed(self, *_):
        if not self.three_d.available:
            return
        axis = self.clip_control.currentIndex() - 1
        if axis < 0:
            self.three_d.clip_plane = None
        else:
            plane = self.three_d._vtk.vtkPlane()
            plane.SetOrigin(*self.crosshair)
            normal = [0, 0, 0]
            normal[axis] = 1
            plane.SetNormal(*normal)
            self.three_d.clip_plane = plane
        self.three_d.refresh()

    def set_mri_overlay(self, volume: Volume):
        if self.source_volume is None:
            raise ValueError("Attach a CT first")
        validate_alignment(self.source_volume, volume)
        self.mri_overlay = resample_to_grid(volume, self.display_volume, labels=False)
        self._render_all()

    def load_volume(self, path: str | Path, *, series_uid=None, assume_spatial_unit=None):
        self.clear_volume()  # Failed loads cannot leave a previous patient on screen.
        source = read_volume(path, series_uid=series_uid, assume_spatial_unit=assume_spatial_unit)
        self.set_volume(source)
        return source

    def set_volume(self, source: Volume):
        """Attach an already validated RAS-mm volume; never infer intensity units."""
        display = orthogonal_resample(source)
        self.clear_volume()
        self.source_volume, self.display_volume = source, display
        self.volume = display.data
        self.three_d.volume = source
        unit = source.intensity_unit
        assumed = " • assumed mm" if source.metadata.get("spatial_units_assumed") else ""
        self.source_label.setText(
            f"{Path(source.source).name if source.source else 'Active volume'}"
            f" • {unit} • RAS mm{assumed}"
        )
        self.reset_view()
        self.three_d.refresh(reset_camera=True)

    def set_case(self, case: CorridorCase, visible_approaches: Iterable[str] | None = None):
        if case.coordinate_frame != "RAS":
            raise ValueError("Viewer requires explicitly converted RAS-mm case geometry")
        previous = self.case
        if previous != case:
            with QtCore.QSignalBlocker(self.approach_display):
                self.approach_display.setCurrentIndex(0)
        if (
            previous is None
            or previous.case_id != case.case_id
            or previous.source_image != case.source_image
        ):
            self.clear_volume()
        if previous != case:
            self.result = self.three_d.result = None
            self._clear_trajectory()
            self.coverage_labels = None
            self.three_d.coverage_labels = None
        self.case = case
        self.visible = (
            {a.name for a in case.approaches}
            if visible_approaches is None
            else set(visible_approaches)
        )
        self.three_d.set_case(case, self.visible)
        if (self.selected_trajectory is not None
                and self.selected_trajectory.approach_name not in self.visible):
            self._clear_trajectory("Selected approach hidden • select a visible feasible witness")
        self._render_all()

    def set_result(self, result):
        self.inspected_target_index = None
        self.result = self.three_d.result = None
        self.coverage_labels = None
        self.three_d.coverage_labels = None
        self._clear_trajectory()
        if result is not None and (self.case is None or result.case_id != self.case.case_id):
            self.three_d.refresh()
            self._render_all()
            raise ValueError("Result does not belong to the active case")
        if result is not None:
            names = {a.name for a in self.case.approaches}
            for approach in result.approaches:
                if approach.name not in names or any(
                    i < 0 or i >= len(self.case.target.points_mm)
                    for i in approach.reached_point_indices
                ):
                    self.three_d.refresh()
                    self._render_all()
                    raise ValueError("Result approach/target indices do not match the active case")
        self.result = self.three_d.result = result
        candidates = [
            path for name in self.visible for path in self.available_trajectories(name)
        ]
        if candidates:
            best = max(candidates, key=self._trajectory_rank)
            self.select_trajectory(best.approach_name, best.trajectory_index)
            self.focus_selected_trajectory()
        elif result is not None:
            self._clear_trajectory("No complete, unconditional feasible witness available")
        self.three_d.refresh()
        self._render_all()

    def _clear_trajectory(self, message="No selected feasible witness • analysis unavailable"):
        self.selected_trajectory = self.three_d.selected_trajectory = None
        self.path_view.clear(message)
        self.trajectory_label.setText(message)
        for view in self.views:
            view.clear_overlays()

    def available_trajectories(self, approach_name):
        """Return explicit feasible paths with original result trajectory indices."""
        if self.case is None or self.result is None:
            return []
        config = next((a for a in self.case.approaches if a.name == approach_name), None)
        result = next((a for a in self.result.approaches if a.name == approach_name), None)
        if config is None or result is None:
            return []
        return [
            path for path in feasible_paths(result, config.instrument.length_mm)
            if all(0 <= i < len(self.case.target.points_mm) for i in path.reached_point_indices)
        ]

    def select_trajectory(self, approach_name, trajectory_index=None):
        """Select actual witness; index addresses ApproachResult.trajectories.

        None uses the engine's ranking: reached count, clearance, working depth.
        Returns SelectedTrajectory, or None when unavailable/hidden. Does not
        change slice position; call focus_selected_trajectory() explicitly.
        """
        paths = self.available_trajectories(approach_name) if approach_name in self.visible else []
        if trajectory_index is not None:
            paths = [p for p in paths if p.trajectory_index == trajectory_index]
        self._clear_trajectory(f"{approach_name} • selected feasible witness unavailable")
        if paths:
            path = max(paths, key=self._trajectory_rank)
            self.selected_trajectory = self.three_d.selected_trajectory = path
            config = next(a for a in self.case.approaches if a.name == approach_name)
            self.path_view.set_path(
                self.source_volume, path, APPROACH_COLORS[config.kind.value],
                (self.level.value() - self.window.value() / 2,
                 self.level.value() + self.window.value() / 2),
                config.instrument.radius_mm, config.instrument.tip_working_radius_mm,
            )
            self.trajectory_label.setText(
                f"{approach_name} • feasible sampled witness #{path.trajectory_index + 1}"
                f" • working depth {path.length_mm:.1f} mm • RAS mm\n"
                "Dashed = projection (not necessarily in slice); solid = slice-slab intersection."
                " Square = entry; triangle = tip; hollow = projected, filled = in slab."
                " Geometric model only; not a safe-resection estimate."
            )
        self.three_d.refresh()
        self._render_all()
        return self.selected_trajectory

    def _trajectory_rank(self, path):
        result = next(a for a in self.result.approaches if a.name == path.approach_name)
        trajectory = result.trajectories[path.trajectory_index]
        clearance = trajectory.minimum_clearance_mm
        return (
            len(path.reached_point_indices),
            float("inf") if clearance is None else clearance,
            path.length_mm, path.approach_name, -path.trajectory_index,
        )

    def focus_selected_trajectory(self):
        """Centre linked slices at the tip and fit the whole physical path."""
        path = self.selected_trajectory
        if path is None or self.source_volume is None:
            return False
        if getattr(self.case.target, "source", "") == "mask_voxel_centers":
            self.set_crosshair(self.case.target.array().mean(axis=0))
        else:
            self.set_crosshair(path.tip_mm)
        low = np.minimum(path.entry_mm, path.tip_mm) - 15
        high = np.maximum(path.entry_mm, path.tip_mm) + 15
        for view in self.views:
            x, y = view.axes
            view.plot.setRange(xRange=(low[x], high[x]), yRange=(low[y], high[y]), padding=0)
        # Preserve whole-scan axial context; support views inspect the local path.
        volume = self.display_volume
        first = volume.index_to_world(np.full(3, -0.5))
        last = volume.index_to_world(np.asarray(volume.data.shape) - 0.5)
        self.views[0].plot.setRange(
            xRange=(min(first[0], last[0]), max(first[0], last[0])),
            yRange=(min(first[1], last[1]), max(first[1], last[1])), padding=0,
        )
        if self.three_d.available:
            self.three_d.renderer.ResetCamera(
                low[0], high[0], low[1], high[1], low[2], high[2],
            )
            self.three_d._vtk_widget.GetRenderWindow().Render()
        return True

    def set_coverage_labels(self, labels):
        """Optional per-target categories: eea_only, tm_only, both, unreached,
        unavailable. None clears categories; unavailable is neutral, not gray.
        Categories must come from the current result's coverage computation.
        """
        if labels is None:
            self.coverage_labels = None
        else:
            labels = np.asarray(labels, dtype=str)
            allowed = {"eea_only", "tm_only", "both", "unreached", "unavailable"}
            if (self.result is None or self.case is None
                    or labels.shape != (len(self.case.target.points_mm),)
                    or not set(labels) <= allowed):
                raise ValueError("Coverage requires current result and one valid label per target")
            self.coverage_labels = labels.copy()
        self.three_d.coverage_labels = self.coverage_labels
        self.three_d.refresh()
        self._render_all()

    def set_mask(self, layer: str, volume: Volume | None):
        if self.source_volume is None:
            raise ValueError("Attach a source image before anatomical masks")
        if volume is None:
            self.masks.pop(layer, None)
            self.display_masks.pop(layer, None)
            self.editors.pop(layer, None)
        else:
            validate_alignment(self.source_volume, volume)
            validate_labels(volume)
            self.masks[layer] = Volume(volume.data.copy(), volume.affine, "label")
            self.display_masks[layer] = resample_to_grid(
                self.masks[layer], self.display_volume, labels=True
            )
            self.editors.pop(layer, None)
        self.three_d.masks = self.masks
        self.three_d._surface_cache.clear()
        self.three_d.refresh()
        self._render_all()
        self.mask_changed.emit(layer, self.masks.get(layer))

    def edit_mask(self, layer, center_mm, radius_mm, label, **options):
        if self.source_volume is None:
            raise ValueError("Attach a source image before editing")
        editor = self.editors.get(layer)
        if editor is None:
            editor = self.editors[layer] = MaskEditor(self.source_volume, self.masks.get(layer))
        changed = editor.paint(center_mm, radius_mm, label, **options)
        if changed:
            self._publish_editor(layer, editor)
        return changed

    def _publish_editor(self, layer, editor):
        self.set_mask(layer, editor.volume)
        self.editors[layer] = editor

    def undo_mask(self, layer):
        editor = self.editors.get(layer)
        if editor and editor.undo():
            self._publish_editor(layer, editor)
            return True
        return False

    def redo_mask(self, layer):
        editor = self.editors.get(layer)
        if editor and editor.redo():
            self._publish_editor(layer, editor)
            return True
        return False

    def set_pick_mode(self, mode):
        if mode not in {"crosshair", "target", "portal"}:
            raise ValueError("Pick mode must be crosshair, target, or portal")
        self.pick_mode = mode
        self.pick_control.setCurrentText(mode)

    def set_crosshair(self, point):
        if self.display_volume is None:
            return
        point = np.asarray(point, float)
        if point.shape != (3,) or not np.isfinite(point).all():
            raise ValueError("Crosshair must be finite RAS mm")
        index = np.rint(self.display_volume.world_to_index(point)).astype(int)
        index = np.clip(index, 0, np.array(self.volume.shape) - 1)
        self.crosshair = self.display_volume.index_to_world(index)
        for view in self.views:
            with QtCore.QSignalBlocker(view.slider):
                view.slider.setRange(0, self.volume.shape[view.axis] - 1)
                view.slider.setValue(int(index[view.axis]))
                view.slider.setEnabled(True)
        self._render_all()
        self.crosshair_changed.emit(tuple(float(v) for v in self.crosshair))
        if self.clip_control.currentIndex():
            self._clip_changed()

    def pick_in_view(self, plane, x, y):
        if self.display_volume is None:
            return
        point = self.crosshair.copy()
        point[list(self.views[plane].axes)] = (x, y)
        index = self.source_volume.world_to_index(point)
        if np.any(index < -0.5) or np.any(index > np.array(self.source_volume.data.shape) - 0.5):
            return  # Blank corners of an oblique resampling are not anatomy.
        self.set_crosshair(point)
        # Emit the clicked physical location, not the rounded crosshair centre.
        picked = tuple(float(v) for v in point)
        self.point_picked.emit(picked)
        if self.pick_mode == "target":
            self.target_picked.emit(picked)
        elif self.pick_mode == "portal":
            self.portal_picked.emit(picked)

    def reset_view(self):
        if self.display_volume is not None:
            finite = self.volume[np.isfinite(self.volume)]
            low, high = np.percentile(finite, [1, 99]) if len(finite) else (0, 1)
            with QtCore.QSignalBlocker(self.window), QtCore.QSignalBlocker(self.level):
                self.window.setValue(max(float(high - low), 1))
                self.level.setValue(float((high + low) / 2))
            self.set_crosshair(self.display_volume.index_to_world(np.array(self.volume.shape) // 2))
            for view in self.views:
                view.plot.autoRange()
        self.three_d.refresh(reset_camera=True)

    def _opacity_changed(self, value):
        self.three_d.opacity = value / 100
        self.three_d.refresh()
        self._render_all()

    def _render_slice(self, plane, value):
        if self.display_volume is None:
            return
        point = self.crosshair.copy()
        axis = self.views[plane].axis
        point[axis] = (
            self.display_volume.affine[axis, 3] + value * self.display_volume.spacing[axis]
        )
        self.set_crosshair(point)

    def _render_all(self, *_):
        if self.display_volume is None:
            return
        volume = self.display_volume
        lower, upper = volume.bounds(edges=True)
        levels = (
            self.level.value() - self.window.value() / 2,
            self.level.value() + self.window.value() / 2,
        )
        if self.path_view.sample is not None:
            self.path_view.image_item.setLevels(levels)
        for view in self.views:
            axis, (x, y) = view.axis, view.axes
            value = view.slider.value()
            bounds = (lower[x], upper[x], lower[y], upper[y])
            view.set_slice(slice_array(self.volume, axis, value), bounds, levels)
            view.heading.setText(
                f"{view.title} • {('R', 'A', 'S')[axis]} {self.crosshair[axis]:.2f} mm"
            )
            view.coordinates.setText(
                view.orientation_text
            )
            view.coordinates.setToolTip(
                "  ".join(f"{n} {v:.2f}" for n, v in zip(("R", "A", "S"), self.crosshair)) + " mm")
            for line, position in zip(view.crosshairs, self.crosshair[[x, y]]):
                line.setValue(position)
                line.show()
            active_layers = set(self.display_masks) | ({"__MRI"} if self.mri_overlay is not None else set())
            for layer in set(view.mask_items) - active_layers:
                view.plot.removeItem(view.mask_items.pop(layer))
            if self.mri_overlay is not None:
                image = slice_array(self.mri_overlay.data, axis, value)
                finite = image[np.isfinite(image)]
                if finite.size:
                    low, high = np.percentile(finite, [1, 99])
                    gray = np.nan_to_num(np.clip((image - low) / max(high - low, 1e-6), 0, 1))
                    rgba = np.zeros((*image.shape, 4), np.uint8)
                    rgba[..., 0] = (gray * 255).astype(np.uint8)
                    rgba[..., 2] = (gray * 220).astype(np.uint8)
                    rgba[..., 3] = (np.isfinite(image) * 255).astype(np.uint8)
                    if "__MRI" not in view.mask_items:
                        item = pg.ImageItem(axisOrder="row-major")
                        item.setZValue(4)
                        view.mask_items["__MRI"] = item
                        view.plot.addItem(item)
                    item = view.mask_items["__MRI"]
                    item.setImage(rgba, autoLevels=False)
                    item.setOpacity(self.opacity.value() / 100)
                    item.setRect(QtCore.QRectF(lower[x], lower[y], upper[x]-lower[x], upper[y]-lower[y]))
            for layer, mask in self.display_masks.items():
                image = slice_array(mask.data, axis, value)
                rgba = np.zeros((*image.shape, 4), np.uint8)
                for label in np.unique(image):
                    if label:
                        rgba[image == label] = (
                            *LABEL_COLORS[(int(label) - 1) % len(LABEL_COLORS)],
                            255,
                        )
                if layer not in view.mask_items:
                    view.mask_items[layer] = pg.ImageItem(axisOrder="row-major")
                    view.mask_items[layer].setZValue(5)
                    view.plot.addItem(view.mask_items[layer])
                item = view.mask_items[layer]
                item.setImage(rgba, autoLevels=False)
                item.setOpacity(self.opacity.value() / 100)
                item.setRect(
                    QtCore.QRectF(lower[x], lower[y], upper[x] - lower[x], upper[y] - lower[y])
                )
            view.clear_overlays()
            if self.case is None:
                continue
            points = self.case.target.array()
            target_in_slice = in_slice(points, axis, self.crosshair[axis], volume.spacing[axis])
            from corridorkit.desktop.planning_overlays import (
                draw_instrument,
                draw_portal,
                draw_target_slice,
            )
            full_target = getattr(self.case.target, "source", "") == "mask_voxel_centers"
            if full_target:
                view.overlays.extend(draw_target_slice(
                    view.plot, self.case.target, self.coverage_labels,
                    view.axes, self.crosshair[axis], min(volume.spacing),
                ))
            elif self.coverage_labels is None:
                view.plot_points(points[target_in_slice], TARGET_COLOR)
            elif not full_target:
                for label, color in {
                    "eea_only": APPROACH_COLORS["eea"],
                    "tm_only": APPROACH_COLORS["transmaxillary"],
                    "both": (180, 100, 230), "unreached": (145, 150, 160),
                    "unavailable": (235, 220, 155),
                }.items():
                    view.plot_points(
                        points[target_in_slice & (self.coverage_labels == label)], color,
                        "x" if label == "unavailable" else "o",
                    )
            for structure in self.case.protected_structures:
                geometry = structure.geometry
                if geometry is not None and geometry.kind == "sphere":
                    distance = geometry.center_mm[axis] - self.crosshair[axis]
                    if abs(distance) < geometry.radius_mm:
                        view.ellipse(
                            geometry.center_mm,
                            np.sqrt(geometry.radius_mm**2 - distance**2),
                            PROTECTED_COLOR,
                        )
            for approach in self.case.approaches:
                if approach.name in self.visible:
                    portal_items = draw_portal(
                        view.plot, approach.portal.center_mm, approach.portal.normal,
                        approach.portal.radius_mm, view.axes,
                        color=APPROACH_COLORS[approach.kind.value],
                        label=f"{'EEA' if approach.kind.value == 'eea' else 'TM'} entry Ø{2 * approach.portal.radius_mm:g} mm",
                    )
                    for item in portal_items:
                        if getattr(item, "planning_kind", "").endswith("_label"):
                            item.hide()
                    view.overlays.extend(portal_items)
                    if self.corridor_toggle.isChecked():
                        from corridorkit.desktop.corridor_overlay import (
                            draw_sampled_corridor,
                        )
                        paths = self.available_trajectories(approach.name)
                        view.overlays.extend(draw_sampled_corridor(
                            view.plot, paths, approach.instrument.radius_mm,
                            view.axes, self.crosshair[axis], APPROACH_COLORS[approach.kind.value],
                        ))
                    point = np.array([approach.portal.center_mm])
                    view.plot_points(
                        point[in_slice(point, axis, self.crosshair[axis], volume.spacing[axis])],
                        APPROACH_COLORS[approach.kind.value],
                        "s",
                    )
            for planned in self.intended_paths:
                if planned.approach_name not in self.visible:
                    continue
                config = next((a for a in self.case.approaches if a.name == planned.approach_name), None)
                if config is None:
                    continue
                items = draw_instrument(
                    view.plot, planned.entry_mm, planned.tip_mm, config.instrument.radius_mm,
                    view.axes, self.crosshair[axis], color=APPROACH_COLORS[config.kind.value],
                )
                for item in items:
                    if getattr(item, "planning_kind", "") == "instrument_projection_label":
                        item.hide()
                view.overlays.extend(items)
            if self.result is not None and self.coverage_labels is None:
                for result in self.result.approaches:
                    if (result.name in self.visible
                            and getattr(result, "status", "complete") == "complete"):
                        reached = points[result.reached_point_indices]
                        view.plot_points(
                            reached[
                                in_slice(reached, axis, self.crosshair[axis], volume.spacing[axis])
                            ],
                            WITNESS_COLOR,
                            "+",
                        )
            if self.selected_trajectory is not None:
                # Show one representative path for each other visible approach
                # alongside the selected path; all remain sampled witnesses.
                for name in sorted(self.visible):
                    if name == self.selected_trajectory.approach_name:
                        continue
                    paths = self.available_trajectories(name)
                    target_index = getattr(self, "inspected_target_index", None)
                    if target_index is not None:
                        paths = [p for p in paths if target_index in p.reached_point_indices]
                    if not paths:
                        continue
                    other = max(paths, key=self._trajectory_rank)
                    other_config = next(a for a in self.case.approaches if a.name == name)
                    view.trajectory_items = {}
                    view.draw_trajectory(
                        other, APPROACH_COLORS[other_config.kind.value],
                        self.crosshair[axis], volume.spacing[axis],
                        other_config.instrument.radius_mm,
                    )
                view.trajectory_items = {}
                config = next(a for a in self.case.approaches
                              if a.name == self.selected_trajectory.approach_name)
                view.draw_trajectory(
                    self.selected_trajectory, APPROACH_COLORS[config.kind.value],
                    self.crosshair[axis], volume.spacing[axis], config.instrument.radius_mm,
                )
                instrument_items = draw_instrument(
                    view.plot, self.selected_trajectory.entry_mm,
                    self.selected_trajectory.tip_mm, config.instrument.radius_mm,
                    view.axes, self.crosshair[axis],
                    color=APPROACH_COLORS[config.kind.value],
                )
                for item in instrument_items:
                    if getattr(item, "planning_kind", "") == "instrument_projection_label":
                        item.hide()
                view.overlays.extend(instrument_items)
