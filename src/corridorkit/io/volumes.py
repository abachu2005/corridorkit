"""Image geometry boundary: xyz arrays, voxel-centre affines, RAS millimetres.

File loaders never infer HU from an integer datatype or a filename. NIfTI/NRRD
without spatial units require ``assume_spatial_unit="mm"`` explicitly. DICOM
directories require a series UID even when only one series is present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path

import numpy as np
from scipy.ndimage import affine_transform

LPS_TO_RAS = np.diag([-1.0, -1.0, 1.0, 1.0])
UNIT_TO_MM = {
    "mm": 1.0,
    "millimeter": 1.0,
    "millimeters": 1.0,
    "meter": 1000.0,
    "m": 1000.0,
    "micron": 0.001,
    "um": 0.001,
    "cm": 10.0,
}


@dataclass
class Volume:
    """Scalar xyz data and voxel-centre-to-RAS-mm affine (not necessarily axial)."""

    data: np.ndarray
    affine: np.ndarray
    intensity_unit: str = "unknown"
    source: str | None = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.data = np.asarray(self.data)
        self.affine = np.asarray(self.affine, dtype=float).copy()
        if self.data.ndim != 3 or min(self.data.shape) < 1:
            raise ValueError("A nonempty scalar 3-D volume is required")
        if self.data.dtype.kind not in "biuf":
            raise ValueError("Volume data must be real numeric scalars")
        if (
            self.affine.shape != (4, 4)
            or not np.isfinite(self.affine).all()
            or not np.allclose(self.affine[3], [0, 0, 0, 1])
            or abs(np.linalg.det(self.affine[:3, :3])) < 1e-12
        ):
            raise ValueError("A finite, invertible homogeneous affine is required")

    @property
    def spacing(self) -> np.ndarray:
        return np.linalg.norm(self.affine[:3, :3], axis=0)

    def index_to_world(self, indices):
        return np.asarray(indices) @ self.affine[:3, :3].T + self.affine[:3, 3]

    def world_to_index(self, points):
        inverse = np.linalg.inv(self.affine)
        return np.asarray(points) @ inverse[:3, :3].T + inverse[:3, 3]

    def bounds(self, *, edges: bool = False):
        """World-axis bounding box; edges includes the half-voxel extent."""
        corners = np.array(
            list(product(*[(-0.5, n - 0.5) if edges else (0.0, n - 1.0) for n in self.data.shape]))
        )
        world = self.index_to_world(corners)
        return world.min(axis=0), world.max(axis=0)


def validate_alignment(reference: Volume, mask: Volume, *, atol_mm: float = 1e-4):
    """Reject differently sampled masks: registration is never inferred."""
    if reference.data.shape != mask.data.shape or not np.allclose(
        reference.affine, mask.affine, atol=atol_mm, rtol=0
    ):
        raise ValueError("Mask grid/affine does not match the source volume")


def validate_labels(volume: Volume):
    if (
        not np.isfinite(volume.data).all()
        or np.any(volume.data < 0)
        or np.any(volume.data != np.floor(volume.data))
    ):
        raise ValueError("Masks require finite, nonnegative integer labels")


def resample_to_grid(volume: Volume, reference: Volume, *, labels=False) -> Volume:
    """Explicit resampling, NOT registration. Labels always use nearest neighbour."""
    if labels:
        validate_labels(volume)
    mapping = np.linalg.inv(volume.affine) @ reference.affine
    rounded = np.rint(mapping)
    mapping = np.where(np.abs(mapping - rounded) < 1e-10, rounded, mapping)
    data = affine_transform(
        volume.data if labels else volume.data.astype(np.float32),
        mapping[:3, :3],
        offset=mapping[:3, 3],
        output_shape=reference.data.shape,
        order=0 if labels else 1,
        mode="constant",
        cval=0 if labels else np.nan,
        prefilter=False,
    )
    return Volume(
        data, reference.affine, volume.intensity_unit, volume.source, dict(volume.metadata)
    )


def orthogonal_resample(
    volume: Volume, *, spacing_mm=None, labels=False, max_voxels=128_000_000
) -> Volume:
    """Resample flips, permutations, shear and obliquity onto positive RAS axes.

    Axis-aligned input retains its exact voxel-centre grid. Oblique input covers
    all source voxel centres; exterior samples are NaN, never invented anatomy.
    """
    linear = volume.affine[:3, :3]
    direction = linear / volume.spacing
    axis_aligned = np.allclose(
        np.sort(np.abs(direction), axis=0), np.array([[0.0], [0.0], [1.0]]), atol=1e-6
    )
    if spacing_mm is None:
        spacing = np.abs(linear).max(axis=1) if axis_aligned else np.repeat(volume.spacing.min(), 3)
    else:
        spacing = np.broadcast_to(np.asarray(spacing_mm, dtype=float), (3,))
    if not np.isfinite(spacing).all() or np.any(spacing <= 0):
        raise ValueError("Display spacing must be finite positive millimetres")
    lower, upper = volume.bounds()
    shape = np.ceil(np.maximum(0, (upper - lower) / spacing - 1e-7)).astype(int) + 1
    if np.prod(shape.astype(float)) > max_voxels:
        raise ValueError("Display grid too large; specify coarser spacing_mm")
    affine = np.diag([*spacing, 1.0])
    affine[:3, 3] = lower
    # A zero-stride placeholder avoids allocating an extra reference-sized array.
    reference = Volume(np.broadcast_to(np.uint8(0), tuple(shape)), affine)
    return resample_to_grid(volume, reference, labels=labels)


def _unit_scale(unit: str | None, assume: str | None):
    selected = unit if unit and unit != "unknown" else assume
    if selected not in UNIT_TO_MM:
        raise ValueError("Unknown spatial units; explicitly supply assume_spatial_unit")
    return UNIT_TO_MM[selected]


def _read_nrrd_geometry(path: Path, assume: str | None):
    """Read only the small NRRD header; SimpleITK decodes the pixel payload."""
    fields = {}
    with path.open("rb") as stream:
        if not stream.readline().startswith(b"NRRD"):
            raise ValueError("Invalid NRRD header")
        for _ in range(1024):
            line = stream.readline(65536)
            if line in (b"\n", b"\r\n", b""):
                break
            text = line.decode("ascii", errors="strict").strip()
            if text and not text.startswith("#") and ":" in text:
                key, value = text.split(":", 1)
                fields[key.lower()] = value.strip()
        else:
            raise ValueError("NRRD header too large")
    if fields.get("dimension") != "3":
        raise ValueError("Only scalar 3-D NRRD is supported")
    space = fields.get("space", "").lower()
    bases = {
        "left-posterior-superior": (-1, -1, 1),
        "lps": (-1, -1, 1),
        "right-anterior-superior": (1, 1, 1),
        "ras": (1, 1, 1),
    }
    if space not in bases:
        raise ValueError("NRRD must declare a supported anatomical RAS or LPS space")
    units = re.findall(r'"([^"]+)"', fields.get("space units", ""))
    if units and len(units) != 3:
        raise ValueError("NRRD requires three spatial units")
    scales = [_unit_scale(unit, assume) for unit in (units or [None] * 3)]
    directions = re.findall(r"\(([^)]+)\)", fields.get("space directions", ""))
    if len(directions) != 3:
        raise ValueError("NRRD requires three spatial direction vectors")
    affine = np.eye(4)
    affine[:3, :3] = np.array([[float(v) for v in x.split(",")] for x in directions]).T
    origin = fields.get("space origin", "").strip("()")
    affine[:3, 3] = [float(v) for v in origin.split(",")]
    affine[:3] = np.diag(np.array(bases[space]) * scales) @ affine[:3]
    return affine, {"spatial_units_assumed": not bool(units)}


def load_volume(
    path: str | Path, *, series_uid: str | None = None, assume_spatial_unit: str | None = None
) -> Volume:
    """Load NIfTI/NRRD or explicitly selected conventional CT DICOM into RAS mm."""
    path = Path(path)
    if path.is_dir():
        if not series_uid:
            raise ValueError("DICOM requires explicit series_uid; call inspect_dicom_series")
        return load_dicom_series(path, series_uid)
    if path.name.lower().endswith((".nii", ".nii.gz")):
        import nibabel as nib

        image = nib.load(str(path))
        if len(image.shape) != 3:
            raise ValueError("Only scalar 3-D NIfTI is supported")
        unit = image.header.get_xyzt_units()[0]
        scale = _unit_scale(unit, assume_spatial_unit)
        qform, qcode = image.get_qform(coded=True)
        sform, scode = image.get_sform(coded=True)
        if not qcode and not scode:
            raise ValueError("NIfTI must declare a qform or sform anatomical affine")
        if qcode and scode and not np.allclose(qform, sform, atol=1e-4, rtol=1e-5):
            raise ValueError("NIfTI qform/sform disagree; resolve the anatomical transform")
        affine = image.affine.copy()
        affine[:3] *= scale
        return Volume(
            np.asarray(image.dataobj),
            affine,
            source=str(path),
            metadata={"spatial_units_assumed": unit == "unknown"},
        )
    if path.suffix.lower() in {".nrrd", ".nhdr"}:
        import SimpleITK as sitk

        affine, metadata = _read_nrrd_geometry(path, assume_spatial_unit)
        image = sitk.ReadImage(str(path))
        if image.GetDimension() != 3 or image.GetNumberOfComponentsPerPixel() != 1:
            raise ValueError("Only scalar 3-D NRRD is supported")
        return Volume(
            sitk.GetArrayFromImage(image).transpose(2, 1, 0),
            affine,
            source=str(path),
            metadata=metadata,
        )
    raise ValueError("Supported image sources: NIfTI, NRRD, or DICOM series directory")


@dataclass(frozen=True)
class DicomSeries:
    series_uid: str
    description: str
    files: tuple[str, ...]
    valid: bool
    error: str | None = None


def _dicom_headers(directory):
    import pydicom

    groups = {}
    for path in sorted(Path(directory).rglob("*")):
        if not path.is_file():
            continue
        try:
            ds = pydicom.dcmread(str(path), stop_before_pixels=True)
        except (pydicom.errors.InvalidDicomError, OSError):
            continue
        if "SeriesInstanceUID" in ds:
            groups.setdefault(str(ds.SeriesInstanceUID), []).append((str(path), ds))
    return groups


def _validate_ct(items):
    """Reject localizers, enhanced CT, mixed frames, irregular grids and gantry shear."""
    if len(items) < 2:
        raise ValueError("Conventional CT requires at least two slices")
    first = items[0][1]
    required = (
        "ImageOrientationPatient",
        "ImagePositionPatient",
        "PixelSpacing",
        "Rows",
        "Columns",
        "FrameOfReferenceUID",
        "StudyInstanceUID",
        "SOPInstanceUID",
        "RescaleSlope",
        "RescaleIntercept",
    )
    for _, ds in items:
        if any(key not in ds for key in required):
            raise ValueError("CT slice is missing required geometry/identity/rescale tags")
        if str(ds.get("SOPClassUID", "")) != "1.2.840.10008.5.1.4.1.1.2":
            raise ValueError("Only conventional single-frame CT Image Storage is supported")
        if ds.get("Modality") != "CT" or int(ds.get("NumberOfFrames", 1)) != 1:
            raise ValueError("Only conventional single-frame CT is supported")
        if any(x in str(ds.get("ImageType", "")).upper() for x in ("LOCALIZER", "SCOUT")):
            raise ValueError("Localizer/scout images are not a CT volume")
        if (
            int(ds.get("SamplesPerPixel", 1)) != 1
            or ds.get("PhotometricInterpretation") != "MONOCHROME2"
        ):
            raise ValueError("Only scalar MONOCHROME2 CT is supported")
        if str(ds.get("RescaleType", "HU")).upper() != "HU":
            raise ValueError("CT rescale units must be HU")
        if ds.get("ModalityLUTSequence") or ds.get("RealWorldValueMappingSequence"):
            raise ValueError("Nonlinear or additional CT value mappings are unsupported")
        for key in ("FrameOfReferenceUID", "StudyInstanceUID", "Rows", "Columns"):
            if ds[key].value != first[key].value:
                raise ValueError(f"Mixed CT {key}")
        for key in ("ImageOrientationPatient", "PixelSpacing"):
            if not np.allclose(
                np.asarray(ds[key].value, float),
                np.asarray(first[key].value, float),
                atol=1e-5,
                rtol=0,
            ):
                raise ValueError(f"Inconsistent CT {key}")
        if (
            not np.isfinite([float(ds.RescaleSlope), float(ds.RescaleIntercept)]).all()
            or float(ds.RescaleSlope) <= 0
        ):
            raise ValueError("Invalid CT intensity rescale")
    if len({str(ds.SOPInstanceUID) for _, ds in items}) != len(items):
        raise ValueError("Duplicate CT SOP instances")
    orientation = np.asarray(first.ImageOrientationPatient, float).reshape(2, 3)
    spacing = np.asarray(first.PixelSpacing, float)
    if (
        not np.isfinite(orientation).all()
        or not np.allclose(orientation @ orientation.T, np.eye(2), atol=1e-5)
        or not np.isfinite(spacing).all()
        or np.any(spacing <= 0)
    ):
        raise ValueError("Invalid CT direction/spacing")
    normal = np.cross(*orientation)
    positions = np.array([ds.ImagePositionPatient for _, ds in items], float)
    if not np.isfinite(positions).all():
        raise ValueError("Invalid CT slice positions")
    order = np.argsort(positions @ normal)
    positions = positions[order]
    distances = np.diff(positions @ normal)
    step = float(np.median(distances))
    if (
        step <= 1e-5
        or not np.allclose(distances, step, atol=1e-3, rtol=1e-4)
        or not np.allclose(np.diff(positions, axis=0), normal * step, atol=1e-3, rtol=1e-4)
    ):
        raise ValueError("Duplicate/missing/irregular slices or gantry-tilted CT")
    affine = np.eye(4)
    affine[:3, :3] = np.column_stack(
        (orientation[0] * spacing[1], orientation[1] * spacing[0], normal * step)
    )
    affine[:3, 3] = positions[0]
    return [items[i] for i in order], LPS_TO_RAS @ affine


def inspect_dicom_series(directory: str | Path) -> list[DicomSeries]:
    """Inspect all series without pixel decoding; return rejection reasons for UI choice."""
    results = []
    for uid, items in _dicom_headers(directory).items():
        error = None
        try:
            ordered, _ = _validate_ct(items)
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            ordered, error = items, str(exc)
        results.append(
            DicomSeries(
                uid,
                str(items[0][1].get("SeriesDescription", "")),
                tuple(path for path, _ in ordered),
                error is None,
                error,
            )
        )
    return results


def load_dicom_series(directory: str | Path, series_uid: str) -> Volume:
    import pydicom

    groups = _dicom_headers(directory)
    if series_uid not in groups:
        raise ValueError("Selected DICOM series was not found")
    ordered, affine = _validate_ct(groups[series_uid])
    slices = []
    for path, _ in ordered:
        ds = pydicom.dcmread(path)
        array = ds.pixel_array
        if array.shape != (int(ds.Rows), int(ds.Columns)):
            raise ValueError("CT decoded pixels disagree with declared dimensions")
        slices.append(
            array.astype(np.float32) * float(ds.RescaleSlope) + float(ds.RescaleIntercept)
        )
    return Volume(
        np.stack(slices, axis=2).transpose(1, 0, 2),
        affine,
        "HU",
        str(directory),
        {
            "series_uid": series_uid,
            "frame_of_reference_uid": str(ordered[0][1].FrameOfReferenceUID),
        },
    )
