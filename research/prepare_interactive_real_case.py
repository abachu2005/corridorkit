#!/usr/bin/env python3
"""Prepare a local real-CT editing example, never a surgical feasibility claim.

No download is performed. Requires the already cached, checksum-verified P001
desktop-smoke conversion and NasalSeg v2 archive. Run with ``--output DIRECTORY``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import nibabel as nib
import numpy as np

from corridorkit.domain.models import (
    ApproachConfig, ApproachKind, CorridorCase, ImageReference, KnowledgeStatus,
    PortalDisk, ProtectedStructure, RigidInstrument, SamplingConfig,
)
from corridorkit.export.json import atomic_json_write, file_sha256
from corridorkit.io.masks import target_from_mask
from corridorkit.io.volumes import load_volume, validate_alignment, validate_labels

CASE_FILENAME = "interactive-real-case.json"
TARGET_FILENAME = "demonstration-roi-NOT-TUMOR.nii.gz"
DEFAULT_OUTPUT = ROOT / "research/data-cache/interactive-real-case"
ARCHIVE_SHA256 = "60c6facf843685802c39e4adff4a05c081c1c4b6175c9cb573745c55abb0fa6a"
SOURCE_LABEL = 1
CROP_START = (31, 85, 21)
CROP_STOP = (38, 92, 28)
WARNING = (
    "REAL CT; demonstration ROI from source air-space label 1, NOT TUMOR. "
    "Example portals are unreviewed computational controls, NOT anatomical "
    "surgical entries. Critical anatomy is missing: clearance unavailable; "
    "analysis must abstain. Software interaction demonstration only, "
    "not a patient study, clinical approval, or surgical recommendation."
)


def write_interactive_real_case(
    output: str | Path = DEFAULT_OUTPUT,
    *,
    cache_directory: str | Path = ROOT / "research/data-cache/desktop-smoke",
) -> Path:
    """Write an envelope, full-grid ROI and README; return the case JSON path.

    The fixed P001 crop includes *every* original label-1 voxel in the declared
    half-open xyz box, at original spacing. There is no stride, resampling,
    synthetic target geometry, inferred HU calibration, or invented anatomy.
    Existing output files are refused to protect saved user edits.
    """
    output = Path(output).resolve()
    cache = Path(cache_directory).resolve()
    ct_path, label_path = cache / "ct.nii.gz", cache / "labels.nii.gz"
    conversion_path = cache / "conversion.json"
    for path in (ct_path, label_path, conversion_path, cache / "ct.nrrd",
                 cache / "labels.nrrd"):
        if not path.is_file():
            raise FileNotFoundError(f"Required existing cached input is unavailable: {path}")
    for filename in (CASE_FILENAME, TARGET_FILENAME, "README.md"):
        if (output / filename).exists():
            raise FileExistsError(f"Refusing to overwrite saved demonstration: {output / filename}")

    source_path = ROOT / "research/data-cache/NasalSeg-v2-source.json"
    archive_path = ROOT / "research/data-cache/NasalSeg-v2.zip"
    manifest_path = ROOT / "research/results/airspace-v1/frozen-manifest.json"
    protocol_path = ROOT / "research/NASALSEG_AUDIT_PROTOCOL.md"
    source = json.loads(source_path.read_text())
    conversion = json.loads(conversion_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    if conversion["case_id"] != "P001" or manifest["subject_split"]["P001"] != "development":
        raise ValueError("This fixed example requires the existing development-only P001 conversion")
    if (source["zenodo_record"] != "13893419" or source["version"] != "v2"
            or source["license"] != "CC-BY-4.0" or source["sha256"] != ARCHIVE_SHA256):
        raise ValueError("Cached source record differs from the inspected NasalSeg v2 provenance")
    if file_sha256(archive_path) != ARCHIVE_SHA256:
        raise ValueError("Cached NasalSeg archive checksum mismatch")
    records = {record["role"]: record for record in conversion["conversions"]}
    source_members = {
        "ct": manifest["image_members"]["P001"],
        "labels": manifest["label_members"]["P001"],
    }
    with zipfile.ZipFile(archive_path) as archive:
        for role, member in source_members.items():
            original_hash = hashlib.sha256(archive.read(member)).hexdigest()
            if (file_sha256(cache / f"{role}.nrrd") != original_hash
                    or records[role]["source_sha256"] != original_hash
                    or records[role]["output_sha256"] != file_sha256(cache / f"{role}.nii.gz")
                    or records[role]["assumed_spatial_units"] != "mm"):
                raise ValueError(f"Cached {role} conversion checksum/provenance mismatch")

    ct, labels = load_volume(ct_path), load_volume(label_path)
    validate_alignment(ct, labels)
    validate_labels(labels)
    for role, converted in (("ct", ct), ("labels", labels)):
        original = load_volume(cache / f"{role}.nrrd", assume_spatial_unit="mm")
        validate_alignment(converted, original)
        if not np.array_equal(converted.data, original.data):
            raise ValueError(f"Cached {role} conversion changed original voxel values")
    if not np.isfinite(ct.data).all():
        raise ValueError("Source CT contains nonfinite values")
    if ct.data.shape != (153, 205, 52):
        raise ValueError("P001 native grid differs from the inspected example")
    crop = tuple(slice(start, stop) for start, stop in zip(CROP_START, CROP_STOP))
    roi = np.zeros(labels.data.shape, dtype=np.uint8)
    roi[crop] = labels.data[crop] == SOURCE_LABEL
    if int(roi.sum()) != 343:
        raise ValueError("Inspected native-grid label-1 crop must contain exactly 343 voxels")
    target = target_from_mask(roi, labels.affine, stride=1)

    # Manually configured anterior RAS positions. These are UI example values,
    # not detected nostrils, maxillary windows, reviewed entries or safe paths.
    center = target.array().mean(axis=0)
    approaches = []
    for name, kind, portal in (
        ("UNREVIEWED computational EEA-typed example", ApproachKind.EEA, (130, 315, -530)),
        ("UNREVIEWED computational TM-typed example", ApproachKind.TRANSMAXILLARY,
         (112, 310, -530)),
    ):
        direction = center - np.asarray(portal)
        direction /= np.linalg.norm(direction)
        approaches.append(ApproachConfig(
            name=name, kind=kind,
            portal=PortalDisk(center_mm=portal, normal=tuple(direction), radius_mm=4),
            nominal_direction=tuple(direction),
            instrument=RigidInstrument(length_mm=100, radius_mm=1),
            sampling=SamplingConfig(polar_steps=3, azimuth_steps=8,
                                    adaptive_levels=0, max_target_witnesses=64),
            allow_unknown_anatomy=False,
        ))
    missing = [
        "Left internal carotid artery", "Right internal carotid artery",
        "Left optic nerve", "Right optic nerve", "Other cranial nerves",
    ]
    case = CorridorCase(
        case_id="NasalSeg-v2-P001-REAL-CT-NOT-TUMOR-UNREVIEWED",
        source_image=ImageReference(uri=str(ct_path), modality="CT",
                                    sha256=file_sha256(ct_path)),
        target=target,
        protected_structures=[
            ProtectedStructure(name=name, status=KnowledgeStatus.UNKNOWN) for name in missing
        ] + [
            ProtectedStructure(name="Skull-base bone and entry-to-target boundaries",
                               status=KnowledgeStatus.UNKNOWN, tissue_type="bone")
        ],
        approaches=approaches,
    )
    output.mkdir(parents=True, exist_ok=True)
    roi_path = output / TARGET_FILENAME
    image = nib.Nifti1Image(roi, labels.affine)
    image.header.set_xyzt_units("mm")
    image.header["descrip"] = b"REAL P001 label 1 crop; NOT TUMOR; unreviewed demonstration ROI"
    nib.save(image, roi_path)
    metadata = {
        "warning": WARNING,
        "synthetic": False, "real_scan": True, "patient_study": False,
        "clinical_use": False, "validated_anatomy": False, "anatomy_reviewed": False,
        "target_is_tumor": False, "clearance_available": False,
        "expected_analysis_status": "abstained",
        "missing_critical_anatomy": [item.name for item in case.protected_structures],
        "target_mask_uri": TARGET_FILENAME, "target_mask_sha256": file_sha256(roi_path),
        "source_label_mask_uri": str(label_path),
        "source_label_mask_sha256": file_sha256(label_path),
        "source_label_display_only": True,
        "source_label_semantics": (
            "Source air-space class 1; no specific anatomical class-number mapping asserted. "
            "Dataset documentation describes bilateral maxillary sinuses, bilateral nasal "
            "cavities and nasopharynx. Air-space labels are not protected-structure clearance."
        ),
        "roi": {
            "label_value": SOURCE_LABEL, "index_order": "xyz",
            "crop_start_inclusive": CROP_START, "crop_stop_exclusive": CROP_STOP,
            "selection": "all source voxels equal to label 1 inside the half-open crop",
            "voxel_count": len(target.points_mm), "stride": 1, "resampled": False,
            "source_shape": labels.data.shape, "affine_ras_mm": labels.affine.tolist(),
            "voxel_volume_mm3": target.voxel_volume_mm3,
        },
        "portals": {
            "reviewed": False, "source": "manually configured computational example values",
            "anatomical_entry_claim": False, "coordinate_frame": "RAS",
            "anterior_axis": "+Y", "nominal_direction": "portal to demonstration ROI centroid",
            "warning": "No nostril or maxillary opening was identified or fabricated.",
        },
        "provenance": {
            "dataset": "NasalSeg", "version": "v2", "subject": "P001",
            "partition": "development", "record_url": "https://zenodo.org/records/13893419",
            "concept_doi": "https://doi.org/10.5281/zenodo.12177180",
            "source_record": source, "source_record_sha256": file_sha256(source_path),
            "source_members": source_members, "conversion": conversion,
            "conversion_sha256": file_sha256(conversion_path),
            "manifest_sha256": file_sha256(manifest_path),
            "inspected_dataset_document": "research/NASALSEG_AUDIT_PROTOCOL.md",
            "inspected_dataset_document_sha256": file_sha256(protocol_path),
            "declared_data_license": source["license"],
            "license_evidence": "Existing cached source record and repository dataset protocol",
            "redistribution": (
                "No CT or source label copy is bundled. This local derivative ROI remains "
                "subject to source data terms, not the software license. Before redistributing "
                "data/derivatives, verify the upstream record and fulfill its attribution and "
                "change-notice requirements; this script does not grant redistribution rights."
            ),
            "changes": "Existing NRRD-to-NIfTI conversion; native-grid binary label-1 crop only.",
            "spatial_units": "mm explicitly assumed during existing dataset-specific conversion",
            "intensity_units": "unknown; HU calibration not asserted",
        },
    }
    case_path = output / CASE_FILENAME
    atomic_json_write(case_path, {"case": case.model_dump(mode="json"),
                                  "real_case_metadata": metadata})
    (output / "README.md").write_text(
        "# Real CT interactive software demonstration\n\n"
        f"{WARNING}\n\n"
        "## Actual data and target\n"
        f"- CT: `{ct_path}` (referenced in place, not copied).\n"
        f"- Source labels: `{label_path}` (display context only, not safety anatomy).\n"
        "- Subject P001, development partition of the existing NasalSeg v2 manifest.\n"
        "- ROI: source label 1 intersected with native xyz indices "
        "`[31:38, 85:92, 21:28]` (half-open), exactly 343 original voxels.\n"
        "- Every selected voxel is imported; no resampling, stride or fabricated geometry.\n"
        "- Class-number-to-anatomical-name mapping is not asserted. This is air-space, "
        "NOT TUMOR. Original CT intensity calibration is unknown, not assumed HU.\n\n"
        "## Interaction and safety\n"
        f"Open `{CASE_FILENAME}` to demonstrate draw/edit/save/export on a real scan. "
        "The EEA/TM-typed approach settings are unreviewed computational templates, not "
        "identified surgical entries. Portal centers are manually set at RAS "
        "(130, 315, -530) and (112, 310, -530) mm, anterior (+Y) of the ROI. "
        "Directions point toward its centroid. No clinician approval is recorded. "
        "Carotids, optic/other cranial nerves and bone boundaries remain unknown; "
        "the engine must abstain and report unavailable clearance. A drawn trajectory "
        "is only intended geometry, never a feasible-path finding.\n\n"
        "## Source, license and provenance\n"
        "NasalSeg v2: https://zenodo.org/records/13893419\n\n"
        "Concept DOI: https://doi.org/10.5281/zenodo.12177180\n\n"
        "The existing `NasalSeg-v2-source.json` and "
        "`research/NASALSEG_AUDIT_PROTOCOL.md` declare **CC-BY-4.0**. "
        "This records inspected local evidence, not a newly verified permission or "
        "a license grant. Source data are not covered by the software license. "
        "No full dataset is downloaded and no CT/source label copy is bundled. "
        "Before sharing data or the derivative ROI, verify the upstream license, "
        "supply required original attribution and identify these conversion/crop changes. "
        "The JSON envelope contains source record, archive hash, member names, "
        "conversion/input hashes, exact crop and limitations. "
        "Keep this README and the envelope alongside any local demonstration.\n\n"
        "## Reproduce locally\n"
        "`python research/prepare_interactive_real_case.py --output NEW_DIRECTORY`\n\n"
        "Requires the existing P001 desktop-smoke cache and verified NasalSeg v2 archive. "
        "Existing output files are not overwritten. This is a software example, "
        "not a patient study or clinical validation.\n",
        encoding="utf-8",
    )
    return case_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="New local output directory (existing outputs are protected)")
    args = parser.parse_args()
    print(write_interactive_real_case(args.output))


if __name__ == "__main__":
    main()
