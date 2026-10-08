"""SYNTHETIC planning demonstration, not validated skull-base anatomy.

The CT-like intensities, apertures, ellipsoid and spheres are invented geometric
fixtures, not patient data or clinically realistic anatomy. Only the explicitly
declared spheres are collision obstacles; the decorative CT shell is NOT a bone
segmentation. Reachability is a sampled geometric demonstration, not safe resection.
"""

from __future__ import annotations

from pathlib import Path

import nibabel as nib
import numpy as np

from corridorkit.domain.models import (
    ApproachConfig,
    ApproachKind,
    CorridorCase,
    ImageReference,
    KnowledgeStatus,
    PortalDisk,
    ProtectedStructure,
    RigidInstrument,
    SamplingConfig,
    SphereGeometry,
)
from corridorkit.export.json import atomic_json_write, file_sha256
from corridorkit.io.masks import target_from_mask

SYNTHETIC_WARNING = (
    "SYNTHETIC research planning fixture; NOT validated anatomy. "
    "Invented CT-like intensities, portals, target and protected spheres; "
    "not patient data, not clinically realistic, not for clinical use. "
    "Sampled straight-instrument reachability is not safe resection or an "
    "approach recommendation. Only declared spheres constrain collision; "
    "the display-only CT shell and air spaces are not segmented anatomy."
)

CT_FILENAME = "SYNTHETIC-planning-ct.nii.gz"
TARGET_FILENAME = "SYNTHETIC-planning-target.nii.gz"
CASE_FILENAME = "SYNTHETIC-planning.case.json"


def _write_nifti(path: Path, data: np.ndarray, affine: np.ndarray) -> None:
    image = nib.Nifti1Image(data, affine)
    image.header.set_xyzt_units("mm")
    image.header["descrip"] = b"SYNTHETIC research fixture; NOT validated anatomy; NOT clinical"
    image.set_qform(affine, code=1)
    image.set_sform(affine, code=1)
    nib.save(image, path)


def write_planning_phantom(directory: str | Path) -> Path:
    """Write a small reproducible CT, full target mask and case JSON; return JSON.

    Load with ``read_case(path)`` and analyze with
    ``analyze_case(case, base_directory=path.parent)``. Image URIs are relative to
    that directory. The JSON envelope also records safety/provenance metadata and
    the target-mask URI; its ``case`` member is an ordinary ``CorridorCase``.

    Coordinates are RAS millimetres: anterior is +Y; both apertures enter toward
    a posterior (-Y), skull-base-like ellipsoid. The full 2-mm voxel-center
    target has fewer than 500 cells and retains its true 8-mm³ cell weights.
    Direction sampling is intentionally small and not exhaustive.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    affine = np.diag([2.0, 2.0, 2.0, 1.0])
    affine[:3, 3] = (-64.0, -72.0, -40.0)
    shape = (65, 73, 41)
    indices = np.indices(shape, dtype=np.float32)
    x, y, z = indices * 2 + affine[:3, 3, None, None, None]

    # Pure display context: ellipsoidal shell, soft interior and anterior air.
    # The shell is deliberately not declared to be a validated bone obstacle.
    head = (x / 50) ** 2 + (y / 58) ** 2 + (z / 34) ** 2
    ct = np.full(shape, -1000, dtype=np.int16)
    ct[head <= 1] = 35
    ct[(head > 0.86) & (head <= 1)] = 850
    nasal_air = (x / 9) ** 2 + ((y - 25) / 23) ** 2 + (z / 11) ** 2 <= 1
    lateral_air = ((x - 29) / 12) ** 2 + ((y - 23) / 15) ** 2 + (z / 12) ** 2 <= 1
    ct[nasal_air | lateral_air] = -850
    target_mask = (
        ((x - 6) / 14) ** 2 + ((y + 22) / 8) ** 2 + (z / 7) ** 2 <= 1
    ).astype(np.uint8)
    ct[target_mask != 0] = 95
    target = target_from_mask(target_mask, affine, stride=1)

    # Central sphere shadows part of the ellipsoid from the midline aperture.
    # The lateral aperture sees around it; both approaches still have witnesses.
    spheres = [
        ("SYNTHETIC central protected sphere", (6.0, -4.0, 0.0), 5.0),
        ("SYNTHETIC left protected sphere", (-16.0, -12.0, 5.0), 4.0),
        ("SYNTHETIC right superior protected sphere", (22.0, -16.0, 8.0), 3.0),
    ]
    structures = []
    for name, center, radius in spheres:
        structures.append(ProtectedStructure(
            name=name,
            status=KnowledgeStatus.KNOWN,
            geometry=SphereGeometry(center_mm=center, radius_mm=radius),
        ))
        occupied = sum((axis - c) ** 2 for axis, c in zip((x, y, z), center)) <= radius**2
        ct[occupied] = 180

    sampling = SamplingConfig(
        max_angle_deg=35,
        polar_steps=2,
        azimuth_steps=8,
        target_directed=True,
        max_target_witnesses=96,
        adaptive_levels=0,
        max_refinement_directions=0,
        portal_offset_rings=0,
    )
    approaches = []
    for name, kind, entry in (
        ("EEA (SYNTHETIC)", ApproachKind.EEA, (0.0, 42.0, 0.0)),
        ("Transmaxillary (SYNTHETIC)", ApproachKind.TRANSMAXILLARY, (36.0, 30.0, 0.0)),
    ):
        direction = np.asarray((6.0, -22.0, 0.0)) - entry
        direction /= np.linalg.norm(direction)
        approaches.append(ApproachConfig(
            name=name,
            kind=kind,
            portal=PortalDisk(center_mm=entry, normal=tuple(direction), radius_mm=4.0),
            nominal_direction=tuple(direction),
            instrument=RigidInstrument(length_mm=100.0, radius_mm=0.8),
            sampling=sampling,
            target_tolerance_mm=1.0,
            allow_unknown_anatomy=False,
        ))

    ct_path = directory / CT_FILENAME
    mask_path = directory / TARGET_FILENAME
    _write_nifti(ct_path, ct, affine)
    _write_nifti(mask_path, target_mask, affine)
    case = CorridorCase(
        case_id="SYNTHETIC-planning-phantom-v1-NOT-validated-anatomy",
        source_image=ImageReference(
            uri=CT_FILENAME, modality="synthetic", sha256=file_sha256(ct_path),
        ),
        target=target,
        protected_structures=structures,
        approaches=approaches,
    )
    path = directory / CASE_FILENAME
    atomic_json_write(path, {
        "case": case.model_dump(mode="json"),
        "synthetic_metadata": {
            "synthetic": True,
            "validated_anatomy": False,
            "clinical_use": False,
            "patient_data": False,
            "warning": SYNTHETIC_WARNING,
            "coordinate_frame": "RAS",
            "spatial_unit": "mm",
            "intensity_unit": "synthetic CT-like; not calibrated HU",
            "target_mask_uri": TARGET_FILENAME,
            "target_mask_sha256": file_sha256(mask_path),
            "target_sampling": "all foreground voxel centers; stride=1",
            "protected_geometry": "invented analytical spheres only",
            "display_only_geometry": "CT shell and air spaces",
            "portal_sampling": "center-only; aperture footprint enforced",
        },
    })
    return path
