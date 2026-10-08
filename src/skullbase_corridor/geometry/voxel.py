"""Conservative protected-mask collision queries in patient coordinates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np
from scipy.spatial import cKDTree

from skullbase_corridor.domain.models import VoxelGeometry, validate_physical_affine


def load_mask(
    geometry: VoxelGeometry, base_directory: Path | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Load a mask without resampling; file coordinates must match the declaration."""
    path = Path(geometry.uri)
    if not path.is_absolute() and base_directory is not None:
        path = base_directory / path
    affine = np.asarray(validate_physical_affine(geometry.affine), dtype=float)
    file_affine = None
    if path.suffix == ".npy":
        data = np.load(path, allow_pickle=False)
    elif path.suffix == ".npz":
        with np.load(path, allow_pickle=False) as archive:
            if len(archive.files) != 1:
                raise ValueError(f"{path} must contain exactly one array")
            data = archive[archive.files[0]]
    elif path.suffix.lower() in {".nrrd", ".nhdr"}:
        # SimpleITK explicitly returns LPS physical coordinates and z,y,x data.
        # Require a declared anatomical space instead of accepting arbitrary NRRD axes.
        with path.open("rb") as stream:
            header_lines = []
            for _ in range(1024):
                line = stream.readline(8192)
                if line in (b"\n", b"\r\n", b""):
                    break
                header_lines.append(line.decode("ascii", errors="strict").strip().lower())
        spaces = [line.split(":", 1)[1].strip() for line in header_lines
                  if line.startswith("space:")]
        if spaces not in (["left-posterior-superior"], ["right-anterior-superior"],
                          ["lps"], ["ras"]):
            raise ValueError("NRRD must explicitly declare RAS or LPS anatomical space")
        import SimpleITK as sitk
        try:
            image = sitk.ReadImage(str(path))
        except RuntimeError as exc:
            raise ValueError(f"invalid NRRD mask: {exc}") from exc
        if image.GetDimension() != 3 or image.GetNumberOfComponentsPerPixel() != 1:
            raise ValueError("NRRD mask must be scalar and three-dimensional")
        data = sitk.GetArrayFromImage(image).transpose(2, 1, 0)
        file_affine = np.eye(4)
        file_affine[:3, :3] = np.asarray(image.GetDirection()).reshape(3, 3) @ np.diag(
            image.GetSpacing()
        )
        file_affine[:3, 3] = image.GetOrigin()
        if geometry.coordinate_frame == "RAS":
            file_affine = np.diag([-1., -1., 1., 1.]) @ file_affine
    else:
        try:
            image = nib.load(path)
        except nib.filebasedimages.ImageFileError as exc:
            raise ValueError(f"invalid protected mask: {exc}") from exc
        data = np.asanyarray(image.dataobj)
        file_affine = image.affine
        if geometry.coordinate_frame == "LPS":
            file_affine = np.diag([-1., -1., 1., 1.]) @ file_affine
    if data.ndim != 3 or any(size == 0 for size in data.shape):
        raise ValueError(f"protected mask {path} must be nonempty and three-dimensional")
    if not np.issubdtype(data.dtype, np.number) and data.dtype != np.bool_:
        raise ValueError("mask must contain numeric labels")
    if not np.all(np.isfinite(data)):
        raise ValueError("protected mask must contain finite values")
    if file_affine is not None and not np.allclose(file_affine, affine, rtol=0, atol=1e-5):
        raise ValueError("protected mask file affine does not match supplied affine/frame")
    foreground = (np.isin(data, geometry.foreground_values)
                  if geometry.foreground_values is not None else data.astype(bool))
    return foreground, affine


@dataclass(frozen=True)
class VoxelCollisionResult:
    clearance_mm: float | None
    out_of_fov: bool
    refined: bool = False
    refinement_budget_exceeded: bool = False


class VoxelMaskBackend:
    """Conservative centerline-to-voxel distance backend.

    Foreground voxels are conservatively enclosed by balls whose radius is half
    the affine voxel-cell diagonal. The centerline is sampled at no more than
    half the smallest affine singular value and the half sample spacing is
    subtracted from clearance. Thus interpolation cannot turn a collision
    between samples into a reported positive clearance.

    Optional bounded refinement checks closed affine voxel cells, not inferred
    anatomical surfaces. Refined clearance is capped at 0.1 mm and guarded
    against roundoff; negative values are not signed penetration distances.
    """

    def __init__(
        self, geometry: VoxelGeometry, *, base_directory: Path | None = None,
        removal: VoxelGeometry | None = None, allow_bone_removal: bool = False,
    ):
        foreground, affine = load_mask(geometry, base_directory)
        if removal is not None:
            if not allow_bone_removal:
                raise ValueError("virtual bone removal requires explicit permission")
            removed, removal_affine = load_mask(removal, base_directory)
            if (removed.shape != foreground.shape
                    or removal.coordinate_frame != geometry.coordinate_frame
                    or not np.allclose(removal_affine, affine, rtol=0, atol=1e-6)):
                raise ValueError("virtual bone removal must match bone shape, affine, and frame")
            if np.any(removed & ~foreground):
                raise ValueError("virtual bone removal must be a subset of the named bone mask")
            foreground = foreground & ~removed
        indices = np.argwhere(foreground)
        self.refine_near_boundary = geometry.refine_near_boundary
        self.max_refinement_cells = geometry.max_refinement_cells
        self.refinement_attempts = 0
        self.refinement_completed = 0
        self.refinement_budget_exceeded = 0
        self.cell_indices = indices if self.refine_near_boundary else None
        self.shape = np.asarray(foreground.shape, dtype=float)
        self.affine = affine
        self.inverse = np.linalg.inv(affine)
        linear = affine[:3, :3]
        self.sample_step_mm = max(float(np.min(np.linalg.svd(linear, compute_uv=False))) / 2, 1e-6)
        self.voxel_bound_radius_mm = 0.5 * max(
            np.linalg.norm(linear @ corner)
            for corner in (
                np.array([x, y, z], dtype=float)
                for x in (-1, 1)
                for y in (-1, 1)
                for z in (-1, 1)
            )
        )
        if len(indices):
            homogeneous = np.column_stack((indices, np.ones(len(indices))))
            centers = (affine @ homogeneous.T).T[:, :3]
            self.tree: cKDTree | None = cKDTree(centers)
        else:
            self.tree = None

    def capsule_clearance(
        self, start: np.ndarray, end: np.ndarray, shaft_radius_mm: float
    ) -> VoxelCollisionResult:
        start, end = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
        if (start.shape != (3,) or end.shape != (3,) or not np.all(np.isfinite(start))
                or not np.all(np.isfinite(end)) or not np.isfinite(shaft_radius_mm)
                or shaft_radius_mm < 0):
            raise ValueError("capsule endpoints and nonnegative radius must be finite")
        # An affine FOV eroded by a Euclidean ball is convex: testing both
        # endpoints proves containment of the whole capsule. Do this before
        # allocating centerline samples, including for arbitrarily distant
        # out-of-FOV endpoints. Subtract translation before transforming to
        # reduce cancellation with large physical coordinate offsets.
        voxel = ((np.stack((start, end)) - self.affine[:3, 3])
                 @ self.inverse[:3, :3].T)
        # Voxel centers occupy index-space cells [-0.5, shape-0.5]. Touching the
        # image boundary is accepted, but any required shaft radius beyond it is
        # unknown. Each inverse row norm gives its exact face-normal margin,
        # including for sheared and anisotropic cells.
        margin = shaft_radius_mm * np.linalg.norm(self.inverse[:3, :3], axis=1)
        if np.any(voxel < (-0.5 + margin)) or np.any(voxel > (self.shape - 0.5 - margin)):
            return VoxelCollisionResult(clearance_mm=None, out_of_fov=True)
        if self.tree is None:
            return VoxelCollisionResult(clearance_mm=float("inf"), out_of_fov=False)

        length = float(np.linalg.norm(end - start))
        intervals = max(1, int(np.ceil(length / self.sample_step_mm)))
        points = start + np.linspace(0.0, 1.0, intervals + 1)[:, None] * (end - start)
        distances, _ = self.tree.query(points, k=1)
        sampling_guard = length / intervals / 2
        clearance = float(np.min(distances)) - self.voxel_bound_radius_mm
        clearance -= shaft_radius_mm + sampling_guard
        if clearance <= 0 and self.refine_near_boundary:
            return self._refine_clearance(start, end, shaft_radius_mm, length, clearance)
        return VoxelCollisionResult(clearance_mm=clearance, out_of_fov=False)

    def _refine_clearance(self, start, end, radius, length, coarse_clearance):
        from skullbase_corridor.geometry.mesh_reference import VoxelCellReference

        self.refinement_attempts += 1
        ceiling = 0.1
        scale = max(1., float(np.max(np.abs(start))), float(np.max(np.abs(end))),
                    length, self.voxel_bound_radius_mm, radius)
        guard = 1e-9 + 64 * np.finfo(float).eps * scale
        midpoint = start + (end - start) / 2
        # Any cell within radius+ceiling of the segment has its center within
        # this ball by the triangle inequality. Omitted cells therefore have
        # capsule clearance > ceiling. Exact, eps=0 tree queries are required.
        search_radius = length / 2 + self.voxel_bound_radius_mm + radius + ceiling + guard
        count = self.tree.query_ball_point(midpoint, search_radius, eps=0, return_length=True)
        if count > self.max_refinement_cells:
            self.refinement_budget_exceeded += 1
            return VoxelCollisionResult(
                clearance_mm=coarse_clearance, out_of_fov=False,
                refinement_budget_exceeded=True,
            )
        candidates = self.tree.query_ball_point(midpoint, search_radius, eps=0)
        if candidates:
            reference = VoxelCellReference(self.cell_indices[candidates], self.affine)
            distance = reference.segment_distance(start, end)
            refined_clearance = min(distance - radius - guard, ceiling)
        else:
            refined_clearance = ceiling
        self.refinement_completed += 1
        return VoxelCollisionResult(
            clearance_mm=refined_clearance, out_of_fov=False, refined=True,
        )
