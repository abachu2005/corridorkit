"""Display-only physical geometry. No feasibility or clinical inference is made."""

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import map_coordinates


@dataclass(frozen=True)
class SelectedTrajectory:
    approach_name: str
    trajectory_index: int
    entry_mm: np.ndarray
    tip_mm: np.ndarray
    reached_point_indices: tuple[int, ...]

    @property
    def length_mm(self):
        return float(np.linalg.norm(self.tip_mm - self.entry_mm))


def feasible_paths(approach_result, instrument_length_mm):
    """Use explicit, unconditional witnesses only, never a nominal direction.

    Indices are indices in ApproachResult.trajectories, NOT filtered-list indices.
    Missing legacy entry/depth data is unavailable rather than guessed.
    """
    if getattr(approach_result, "status", None) != "complete":
        return []
    paths = []
    for index, trajectory in enumerate(getattr(approach_result, "trajectories", [])):
        if not trajectory.feasible or trajectory.conditional:
            continue
        if trajectory.entry_point_mm is None or trajectory.working_depth_mm is None:
            continue
        entry = np.asarray(trajectory.entry_point_mm, float)
        direction = np.asarray(trajectory.direction, float)
        depth = trajectory.working_depth_mm
        if (
            entry.shape != (3,) or direction.shape != (3,)
            or not np.isfinite(entry).all() or not np.isfinite(direction).all()
            or not np.isfinite(depth) or not 0 < depth <= instrument_length_mm + 1e-7
            or not np.isclose(np.linalg.norm(direction), 1.0, atol=1e-6)
        ):
            continue
        paths.append(SelectedTrajectory(
            approach_result.name, index, entry, entry + direction * depth,
            tuple(trajectory.reached_point_indices),
        ))
    return paths


def project_segment(entry, tip, axes):
    """Orthographic projection of physical RAS-mm endpoints; no voxel indexing."""
    return np.asarray([entry, tip], float)[:, list(axes)]


def clip_segment_to_slab(entry, tip, axis, coordinate, thickness):
    """Return the finite 3-D segment inside a closed physical slice slab or None."""
    entry, tip = np.asarray(entry, float), np.asarray(tip, float)
    if thickness <= 0 or not np.isfinite(thickness):
        raise ValueError("Slab thickness must be positive and finite")
    delta = tip - entry
    low, high = coordinate - thickness / 2, coordinate + thickness / 2
    if abs(delta[axis]) < 1e-12:
        return np.array([entry, tip]) if low <= entry[axis] <= high else None
    near, far = sorted(((low - entry[axis]) / delta[axis], (high - entry[axis]) / delta[axis]))
    start, stop = max(0.0, near), min(1.0, far)
    return None if start > stop else np.array([entry + start * delta, entry + stop * delta])


@dataclass(frozen=True)
class ObliqueSlice:
    image: np.ndarray
    along_mm: np.ndarray
    across_mm: np.ndarray
    origin_mm: np.ndarray
    along_direction: np.ndarray
    across_direction: np.ndarray

    @property
    def bounds(self):
        dx = self.along_mm[1] - self.along_mm[0]
        dy = self.across_mm[1] - self.across_mm[0]
        return (
            self.along_mm[0] - dx / 2, self.along_mm[-1] + dx / 2,
            self.across_mm[0] - dy / 2, self.across_mm[-1] + dy / 2,
        )


def resample_path_plane(volume, entry, tip, *, spacing_mm=None, margin_mm=15.0,
                        half_width_mm=30.0, max_pixels=512):
    """Linear native-affine sampling of a plane containing the finite path.

    x is distance from entry along path; y is transverse distance. The transverse
    axis is the least-aligned RAS axis projected perpendicular to the path.
    Exterior of the native voxel-centre interpolation domain remains NaN (FOV
    background), not zero-valued anatomy. Memory is bounded by max_pixels**2.
    """
    entry, tip = np.asarray(entry, float), np.asarray(tip, float)
    if entry.shape != (3,) or tip.shape != (3,) or not np.isfinite([entry, tip]).all():
        raise ValueError("Endpoints must be finite RAS-mm triples")
    length = float(np.linalg.norm(tip - entry))
    spacing = float(volume.spacing.min() if spacing_mm is None else spacing_mm)
    if (length <= 1e-9 or not np.isfinite(spacing) or spacing <= 0
            or not np.isfinite(margin_mm) or margin_mm < 0
            or not np.isfinite(half_width_mm) or half_width_mm <= 0 or max_pixels < 2):
        raise ValueError("Invalid path or sampling dimensions")
    along = (tip - entry) / length
    reference = np.eye(3)[np.argmin(np.abs(along))]
    across = reference - np.dot(reference, along) * along
    across /= np.linalg.norm(across)
    nx = min(max_pixels, max(2, int(np.ceil((length + 2 * margin_mm) / spacing)) + 1))
    ny = min(max_pixels, max(2, int(np.ceil(2 * half_width_mm / spacing)) + 1))
    x = np.linspace(-margin_mm, length + margin_mm, nx)
    y = np.linspace(-half_width_mm, half_width_mm, ny)
    world = entry + x[None, :, None] * along + y[:, None, None] * across
    indices = volume.world_to_index(world)
    # Round tiny floating-point boundary excursions without extending the FOV.
    nearest = np.rint(indices)
    indices = np.where(np.abs(indices - nearest) < 1e-9, nearest, indices)
    image = map_coordinates(
        volume.data, np.moveaxis(indices, -1, 0), output=np.float32,
        order=1, mode="constant", cval=np.nan, prefilter=False,
    )
    return ObliqueSlice(image, x, y, entry, along, across)
