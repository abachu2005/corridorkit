"""Qt-independent, undoable label editing in physical RAS millimetres.

``MaskEditor(source, mask=None, allowed_labels=None, forbidden=None)`` owns a
copy. ``paint(center_mm, radius_mm, label, erase=False, replace=False,
plane_axis=None, plane_thickness_mm=None)`` returns the changed voxel count.
The default brush is a 3-D sphere; a plane brush is a physical circular slab.
Only background/the selected label can change unless replacement is explicit.
``undo/redo`` return bool; ``volume`` exposes the current affine-aware mask.
No edits imply clinical/anatomical approval: ``validate_for_analysis`` performs
geometric checks only; an anatomical reviewer still needs to approve the labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np

from corridorkit.io.volumes import Volume, validate_alignment, validate_labels


@dataclass
class BrushChange:
    indices: tuple[np.ndarray, ...]
    before: np.ndarray
    after: np.ndarray


class MaskEditor:
    def __init__(
        self,
        source: Volume,
        mask: Volume | None = None,
        *,
        allowed_labels: set[int] | None = None,
        forbidden: Volume | None = None,
    ):
        if mask is not None:
            validate_alignment(source, mask)
            validate_labels(mask)
        if forbidden is not None:
            validate_alignment(source, forbidden)
            validate_labels(forbidden)
        self.source = source
        self.volume = Volume(
            np.zeros(source.data.shape, np.uint16)
            if mask is None
            else mask.data.astype(np.uint16)
            if mask.data.dtype.kind == "b"
            else mask.data.copy(),
            source.affine,
            "label",
        )
        self.allowed_labels = allowed_labels
        self.forbidden = forbidden
        self._undo: list[BrushChange] = []
        self._redo: list[BrushChange] = []

    def paint(
        self,
        center_mm,
        radius_mm: float,
        label: int,
        *,
        erase=False,
        replace=False,
        plane_axis: int | None = None,
        plane_thickness_mm: float | None = None,
    ) -> int:
        center = np.asarray(center_mm, float)
        if center.shape != (3,) or not np.isfinite(center).all():
            raise ValueError("Brush centre must be finite RAS millimetres")
        if not np.isfinite(radius_mm) or radius_mm <= 0:
            raise ValueError("Brush radius must be positive millimetres")
        if isinstance(label, bool) or int(label) != label or label <= 0:
            raise ValueError("Select a positive integer anatomical label")
        if self.allowed_labels is not None and label not in self.allowed_labels:
            raise ValueError("Label is not in the configured anatomical label set")
        dtype = self.volume.data.dtype
        if dtype.kind in "iu" and label > np.iinfo(dtype).max:
            raise ValueError("Label exceeds mask datatype capacity")
        index = self.source.world_to_index(center)
        if np.any(index < -0.5) or np.any(index > np.array(self.source.data.shape) - 0.5):
            raise ValueError("Brush centre lies outside the source field of view")
        if plane_axis is not None:
            if plane_axis not in (0, 1, 2):
                raise ValueError("plane_axis must be a RAS axis 0, 1, or 2")
            if (
                plane_thickness_mm is None
                or not np.isfinite(plane_thickness_mm)
                or plane_thickness_mm <= 0
            ):
                raise ValueError("Plane brush requires a positive physical slab thickness")
        extent = np.repeat(radius_mm, 3)
        if plane_axis is not None:
            extent[plane_axis] = plane_thickness_mm / 2
        corners = center + np.array(list(product((-1, 1), repeat=3))) * extent
        limits = self.source.world_to_index(corners)
        lo = np.maximum(0, np.floor(limits.min(axis=0)).astype(int))
        hi = np.minimum(
            np.array(self.source.data.shape) - 1, np.ceil(limits.max(axis=0)).astype(int)
        )
        grid = np.stack(
            np.meshgrid(*[np.arange(a, b + 1) for a, b in zip(lo, hi)], indexing="ij"), axis=-1
        ).reshape(-1, 3)
        delta = self.source.index_to_world(grid) - center
        if plane_axis is None:
            inside = np.sum(delta * delta, axis=1) <= radius_mm**2 + 1e-8
        else:
            inside = np.abs(delta[:, plane_axis]) <= plane_thickness_mm / 2 + 1e-8
            delta[:, plane_axis] = 0
            inside &= np.sum(delta * delta, axis=1) <= radius_mm**2 + 1e-8
        indices = tuple(grid.T)
        current = self.volume.data[indices]
        inside &= np.isfinite(self.source.data[indices])
        if self.forbidden is not None and not erase:
            inside &= self.forbidden.data[indices] == 0
        if erase:
            inside &= current == label
        elif not replace:
            inside &= (current == 0) | (current == label)
        value = 0 if erase else label
        inside &= current != value
        if not inside.any():
            return 0
        indices = tuple(grid[inside].T)
        before = self.volume.data[indices].copy()
        after = np.full_like(before, value)
        self.volume.data[indices] = after
        self._undo.append(BrushChange(indices, before, after))
        self._redo.clear()
        return len(before)

    def undo(self) -> bool:
        if not self._undo:
            return False
        command = self._undo.pop()
        self.volume.data[command.indices] = command.before
        self._redo.append(command)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        command = self._redo.pop()
        self.volume.data[command.indices] = command.after
        self._undo.append(command)
        return True

    def validate_for_analysis(self, *, require_nonempty=True) -> dict:
        validate_alignment(self.source, self.volume)
        validate_labels(self.volume)
        foreground = self.volume.data > 0
        if require_nonempty and not foreground.any():
            raise ValueError("Anatomical mask is empty")
        if np.any(foreground & ~np.isfinite(self.source.data)):
            raise ValueError("Anatomical labels occupy invalid source samples")
        if self.forbidden is not None and np.any(foreground & (self.forbidden.data > 0)):
            raise ValueError("Anatomical mask overlaps forbidden anatomy")
        labels = {int(x) for x in np.unique(self.volume.data[foreground])}
        if self.allowed_labels is not None and not labels <= self.allowed_labels:
            raise ValueError("Mask contains unconfigured anatomical labels")
        return {
            "labels": sorted(labels),
            "voxel_count": int(foreground.sum()),
            "volume_mm3": float(foreground.sum() * abs(np.linalg.det(self.volume.affine[:3, :3]))),
            "anatomical_review": "required",
        }
