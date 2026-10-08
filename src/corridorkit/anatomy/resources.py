"""Versioned catalogue of external anatomy resources.

The catalogue is evidence metadata, not an integration manifest.  In particular,
``ArtifactAvailability.DOWNLOADABLE`` means that the upstream project publishes
an artifact; it does not mean that corridorkit can execute it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


REGISTRY_SCHEMA_VERSION = "1.0.0"


class ResourceRole(StrEnum):
    """Permitted uses of an external resource in this project."""

    PROPOSAL = "proposal"
    REFERENCE = "reference"
    EVALUATION = "evaluation"
    CORRECTION = "correction"
    UNAVAILABLE = "unavailable"


class Modality(StrEnum):
    CT = "ct"
    CTA = "cta"
    CBCT = "cbct"
    MR = "mr"
    MRA = "mra"
    DWI = "dwi"
    SURFACE = "surface"


class ArtifactAvailability(StrEnum):
    """Availability verified at the cited upstream source."""

    DOWNLOADABLE = "downloadable"
    DATA_ONLY = "data_only"
    SOURCE_ONLY = "source_only"
    NO_WEIGHTS = "no_weights"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"


class LicenseStatus(StrEnum):
    VERIFIED = "verified"
    REVIEW_REQUIRED = "review_required"
    UNKNOWN = "unknown"


class SupportStatus(StrEnum):
    """Local application support, independent of upstream availability."""

    SUPPORTED = "supported"
    RESEARCH_ONLY = "research_only"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ModelResource:
    resource_id: str
    name: str
    version: str
    roles: frozenset[ResourceRole]
    modalities: frozenset[Modality]
    relevant_labels: tuple[str, ...]
    artifact_availability: ArtifactAvailability
    artifact_notes: str
    license_id: str
    license_status: LicenseStatus
    source_url: str
    limitations: tuple[str, ...]
    support_status: SupportStatus = SupportStatus.RESEARCH_ONLY
    production_default: bool = False


def _resource(
    resource_id: str,
    name: str,
    *,
    version: str,
    roles: Iterable[ResourceRole],
    modalities: Iterable[Modality],
    labels: Iterable[str],
    availability: ArtifactAvailability,
    artifact_notes: str,
    license_id: str,
    license_status: LicenseStatus,
    source_url: str,
    limitations: Iterable[str],
    support_status: SupportStatus = SupportStatus.RESEARCH_ONLY,
    production_default: bool = False,
) -> ModelResource:
    return ModelResource(
        resource_id=resource_id,
        name=name,
        version=version,
        roles=frozenset(roles),
        modalities=frozenset(modalities),
        relevant_labels=tuple(labels),
        artifact_availability=availability,
        artifact_notes=artifact_notes,
        license_id=license_id,
        license_status=license_status,
        source_url=source_url,
        limitations=tuple(limitations),
        support_status=support_status,
        production_default=production_default,
    )


RESOURCE_REGISTRY: tuple[ModelResource, ...] = (
    _resource(
        "totalsegmentator-head-glands-cavities",
        "TotalSegmentator: head_glands_cavities",
        version="upstream-current; reviewed-2026-10-07",
        roles=[ResourceRole.PROPOSAL],
        modalities=[Modality.CT],
        labels=[
            "optic nerves",
            "eyes and lenses",
            "nasal cavity",
            "nasopharynx",
            "oropharynx",
            "hypopharynx",
            "hard palate",
            "auditory canals",
        ],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes="Upstream CLI downloads task weights; no local adapter or pinned checksum.",
        license_id="Apache-2.0 (open task per upstream task list)",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://github.com/wasserth/TotalSegmentator#available-models",
        limitations=[
            "Upstream weights are not bundled or checksum-pinned.",
            "Outputs require expert review and skull-base validation.",
        ],
    ),
    _resource(
        "totalsegmentator-head-muscles",
        "TotalSegmentator: head_muscles",
        version="upstream-current; reviewed-2026-10-07",
        roles=[ResourceRole.PROPOSAL],
        modalities=[Modality.CT],
        labels=["masseters", "temporalis", "pterygoids", "tongue", "digastrics"],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes="Upstream CLI downloads task weights; no local adapter or pinned checksum.",
        license_id="Apache-2.0 (open task per upstream task list)",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://github.com/wasserth/TotalSegmentator#available-models",
        limitations=["Not a critical-neurovascular segmentation.", "No local inference adapter."],
    ),
    _resource(
        "totalsegmentator-headneck-bones-vessels",
        "TotalSegmentator: headneck_bones_vessels",
        version="upstream-current; reviewed-2026-10-07",
        roles=[ResourceRole.PROPOSAL],
        modalities=[Modality.CT],
        labels=[
            "zygomatic arches",
            "styloid processes",
            "internal carotid arteries",
            "internal jugular veins",
        ],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes="Upstream CLI downloads task weights; no local adapter or pinned checksum.",
        license_id="Apache-2.0 (open task per upstream task list)",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://github.com/wasserth/TotalSegmentator#available-models",
        limitations=[
            "Broad head-and-neck training is not corridor-specific validation.",
            "No local inference adapter.",
        ],
    ),
    _resource(
        "totalsegmentator-headneck-muscles",
        "TotalSegmentator: headneck_muscles",
        version="upstream-current; reviewed-2026-10-07",
        roles=[ResourceRole.PROPOSAL],
        modalities=[Modality.CT],
        labels=["pharyngeal constrictors", "prevertebral muscles", "scalenes"],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes="Upstream CLI downloads task weights; no local adapter or pinned checksum.",
        license_id="Apache-2.0 (open task per upstream task list)",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://github.com/wasserth/TotalSegmentator#available-models",
        limitations=["No skull-base corridor validation.", "No local inference adapter."],
    ),
    _resource(
        "totalsegmentator-craniofacial",
        "TotalSegmentator: craniofacial_structures",
        version="upstream-current; reviewed-2026-10-07",
        roles=[ResourceRole.PROPOSAL],
        modalities=[Modality.CT],
        labels=["skull", "mandible", "upper and lower teeth", "maxillary sinus", "frontal sinus"],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes="Upstream CLI downloads task weights; no local adapter or pinned checksum.",
        license_id="Apache-2.0 (open task per upstream task list)",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://github.com/wasserth/TotalSegmentator#available-models",
        limitations=["No surgical-window labels.", "No local inference adapter."],
    ),
    _resource(
        "totalsegmentator-teeth",
        "TotalSegmentator: teeth",
        version="upstream-current; reviewed-2026-10-07",
        roles=[ResourceRole.PROPOSAL],
        modalities=[Modality.CBCT],
        labels=[
            "upper and lower jawbones", "individual teeth and pulps",
            "left and right maxillary sinuses", "pharynx", "incisive canals",
        ],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes="Upstream task is based on ToothFairy3; weights download through the CLI.",
        license_id="review_required (task/data terms must be reconciled)",
        license_status=LicenseStatus.REVIEW_REQUIRED,
        source_url="https://github.com/wasserth/TotalSegmentator#available-models",
        limitations=["CBCT domain.", "No infraorbital canal or operative-window label."],
    ),
    _resource(
        "nasalseg",
        "NasalSeg",
        version="Zenodo 13893419; reviewed-2026-10-07",
        roles=[ResourceRole.REFERENCE, ResourceRole.EVALUATION],
        modalities=[Modality.CT],
        labels=[
            "left nasal cavity",
            "right nasal cavity",
            "nasopharynx",
            "left maxillary sinus",
            "right maxillary sinus",
        ],
        availability=ArtifactAvailability.DATA_ONLY,
        artifact_notes="Annotated data and code are published; pretrained weights were not verified.",
        license_id="CC-BY-4.0 (dataset)",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://zenodo.org/records/13893419",
        limitations=["Five labels only.", "Dataset license does not establish a model license."],
    ),
    _resource(
        "slicer-automated-dental-tools-amasss",
        "Slicer Automated Dental Tools: AMASSS",
        version="upstream-current; reviewed-2026-10-07",
        roles=[ResourceRole.PROPOSAL],
        modalities=[Modality.CBCT],
        labels=["craniofacial bones", "mandible", "maxilla", "teeth", "airway"],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes="The Slicer module offers 'Download latest models'; artifacts are not pinned.",
        license_id="review_required (code README says Apache-2.0; LICENCE is Slicer terms)",
        license_status=LicenseStatus.REVIEW_REQUIRED,
        source_url="https://github.com/DCBIA-OrthoLab/SlicerAutomatedDentalTools",
        limitations=["Runs through a Slicer extension.", "Model-specific terms need review."],
    ),
    _resource(
        "slicer-automated-dental-tools-ali",
        "Slicer Automated Dental Tools: ALI-CBCT",
        version="upstream-current; reviewed-2026-10-07",
        roles=[ResourceRole.PROPOSAL, ResourceRole.CORRECTION],
        modalities=[Modality.CBCT],
        labels=["craniofacial landmarks"],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes="The Slicer module offers model download; artifacts are not pinned.",
        license_id="review_required (code README says Apache-2.0; model terms unverified)",
        license_status=LicenseStatus.REVIEW_REQUIRED,
        source_url="https://github.com/DCBIA-OrthoLab/SlicerAutomatedDentalTools",
        limitations=["Landmarks, not dense critical-structure masks.", "Requires Slicer workflow."],
    ),
    _resource(
        "toothfairy3",
        "ToothFairy3",
        version="2025 challenge dataset; reviewed-2026-10-07",
        roles=[ResourceRole.REFERENCE, ResourceRole.EVALUATION],
        modalities=[Modality.CBCT],
        labels=[
            "maxilla",
            "mandible",
            "teeth and pulps",
            "inferior alveolar canals",
            "maxillary sinus",
            "pharynx",
            "incisive canals",
            "lingual canal",
        ],
        availability=ArtifactAvailability.DATA_ONLY,
        artifact_notes="Training data require sign-up; no official production weights verified.",
        license_id="CC-BY-NC-SA (training dataset)",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://toothfairy3.grand-challenge.org/dataset/",
        limitations=["Non-commercial share-alike dataset.", "Private test set.", "CBCT only."],
    ),
    _resource(
        "ct-scope",
        "CT-SCOPE",
        version="Zenodo 15085103; reviewed-2026-10-07",
        roles=[ResourceRole.REFERENCE, ResourceRole.EVALUATION],
        modalities=[Modality.CT],
        labels=["osseous structures surrounding the paranasal sinuses"],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes=(
            "Data, implementation and 2-D/3-D/final model artifacts are published; "
            "the final model trained on all released slices and is not independent test evidence."
        ),
        license_id="CC-BY-NC-ND",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://zenodo.org/records/15085103",
        limitations=["Only 13 manually annotated subjects.", "License prohibits derivatives."],
    ),
    _resource(
        "han-seg",
        "HaN-Seg",
        version="1.0 (Zenodo 7442914)",
        roles=[ResourceRole.PROPOSAL, ResourceRole.REFERENCE, ResourceRole.EVALUATION],
        modalities=[Modality.CT, Modality.MR],
        labels=[
            "carotid arteries",
            "optic nerves and chiasm",
            "brainstem",
            "spinal cord",
            "mandible",
            "oral cavity",
            "pituitary",
        ],
        availability=ArtifactAvailability.DATA_ONLY,
        artifact_notes="42 public training cases with reference masks; no production weights.",
        license_id="CC-BY-NC-ND-4.0",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://zenodo.org/records/7442914",
        limitations=["CT and MR are not registered.", "Non-commercial, no-derivatives terms."],
    ),
    _resource(
        "segrap2023",
        "SegRap2023",
        version="2023 challenge; reviewed-2026-10-07",
        roles=[ResourceRole.REFERENCE, ResourceRole.EVALUATION],
        modalities=[Modality.CT],
        labels=[
            "optic nerves and chiasm",
            "internal auditory canals",
            "carotid-adjacent OAR context",
            "pituitary",
            "temporal lobes",
            "tympanic cavities",
        ],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes=(
            "Training labels and third-party challenge inference code/weights are downloadable; "
            "the released implementation requires paired contrast and non-contrast CT."
        ),
        license_id="challenge end-user terms; redistribution prohibited",
        license_status=LicenseStatus.REVIEW_REQUIRED,
        source_url="https://segrap2023.grand-challenge.org/dataset/",
        limitations=["Nasopharyngeal-cancer cohort.", "Dataset license is not a CC software license."],
    ),
    _resource(
        "topcow",
        "TopCoW",
        version="2024 challenge; Zenodo 15692630",
        roles=[ResourceRole.PROPOSAL, ResourceRole.REFERENCE, ResourceRole.EVALUATION],
        modalities=[Modality.CTA, Modality.MRA],
        labels=[
            "internal carotid arteries",
            "anterior cerebral arteries",
            "middle cerebral arteries",
            "posterior cerebral arteries",
            "communicating arteries",
            "basilar artery",
        ],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes=(
            "Paired CTA/MRA annotations, organizer baseline weights, and best-performing "
            "challenge Docker images are public; artifacts are large and not locally pinned."
        ),
        license_id="Open use with attribution; commercial use requires owner permission",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://topcow24.grand-challenge.org/data/",
        limitations=["Circle of Willis only.", "Angiographic input differs from routine CT."],
    ),
    _resource(
        "uw-skullbase-atlas",
        "University of Washington synthetic skull-base CT atlas",
        version="ResearchWorks 2b0f0e1e-ae81-4bf8-9b79-8dc6872b5ecc",
        roles=[ResourceRole.REFERENCE, ResourceRole.EVALUATION],
        modalities=[Modality.CT],
        labels=[
            "septum", "optic nerves", "internal carotid artery", "foramina",
            "vidian canal", "pterygopalatine fossa", "pituitary", "cavernous sinus",
        ],
        availability=ArtifactAvailability.DATA_ONLY,
        artifact_notes=(
            "Original CT, 14-component segmentation and instructions are acquired and "
            "checksum-verified in the local data cache; data are not bundled in source releases."
        ),
        license_id="CC-BY-NC-SA-3.0-US",
        license_status=LicenseStatus.VERIFIED,
        source_url=(
            "https://digital.lib.washington.edu/researchworks/items/"
            "2b0f0e1e-ae81-4bf8-9b79-8dc6872b5ecc/full"
        ),
        limitations=[
            "Synthetic average of six scans, not a patient scan.",
            "ICA annotation spans only 7.5 mm and cannot establish route clearance.",
            "No maxillary window, lesion target, or operative trajectory annotation.",
        ],
    ),
    _resource(
        "alpaca",
        "SlicerMorph ALPACA",
        version="upstream-current; reviewed-2026-10-07",
        roles=[ResourceRole.PROPOSAL, ResourceRole.CORRECTION],
        modalities=[Modality.SURFACE],
        labels=["user-supplied 3D surface landmarks"],
        availability=ArtifactAvailability.SOURCE_ONLY,
        artifact_notes="Algorithmic landmark transfer; it does not use pretrained model weights.",
        license_id="BSD-2-Clause (SlicerMorph code)",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://github.com/SlicerMorph/SlicerMorph",
        limitations=["Requires source meshes and landmarks.", "Not voxel segmentation."],
    ),
    _resource(
        "cntseg",
        "CNTSeg",
        version="paper implementation; reviewed-2026-10-07",
        roles=[ResourceRole.UNAVAILABLE],
        modalities=[Modality.MR, Modality.DWI],
        labels=["optic nerve (CN II)", "CN III", "CN V", "CN VII/VIII"],
        availability=ArtifactAvailability.NO_WEIGHTS,
        artifact_notes="Training code is public; pretrained weights are not provided.",
        license_id="unknown",
        license_status=LicenseStatus.UNKNOWN,
        source_url="https://github.com/IPIS-XieLei/CNTSeg",
        limitations=["Requires T1w, FA, and fODF peaks.", "No downloadable pretrained weights."],
        support_status=SupportStatus.UNAVAILABLE,
    ),
    _resource(
        "opticnerve-seg",
        "OpticNerveSeg",
        version="Zenodo 21348649; reviewed-2026-10-07",
        roles=[ResourceRole.REFERENCE, ResourceRole.EVALUATION],
        modalities=[Modality.MR],
        labels=["optic nerves", "optic chiasm", "optic tracts"],
        availability=ArtifactAvailability.DATA_ONLY,
        artifact_notes="Derived masks/metadata are published; source HCP MRI must be obtained separately.",
        license_id="CC-BY-4.0 annotations; HCP Open Access terms for source MRI",
        license_status=LicenseStatus.VERIFIED,
        source_url="https://zenodo.org/records/21348649",
        limitations=["MRI only.", "No verified pretrained weights.", "HCP source data terms apply."],
    ),
    _resource(
        "sinussegment",
        "SinusSegment",
        version="upstream-current; reviewed-2026-10-07",
        roles=[ResourceRole.UNAVAILABLE],
        modalities=[Modality.CT],
        labels=[
            "maxillary sinuses",
            "anterior and posterior ethmoid sinuses",
            "sphenoid sinus",
            "frontal sinus",
            "nasal cavity",
        ],
        availability=ArtifactAvailability.DOWNLOADABLE,
        artifact_notes="Repository includes default_parameters.pth, but reuse terms are unverified.",
        license_id="unknown (repository); article is CC-BY-NC",
        license_status=LicenseStatus.UNKNOWN,
        source_url="https://github.com/rheadkaul/SinusSegment",
        limitations=["Repository model license is absent or unverified.", "No local adapter or checksum."],
        support_status=SupportStatus.UNAVAILABLE,
    ),
)


def filter_resources(
    *,
    modality: Modality | str | None = None,
    role: ResourceRole | str | None = None,
) -> tuple[ModelResource, ...]:
    """Return registry entries matching all supplied filters."""

    wanted_modality = Modality(modality) if modality is not None else None
    wanted_role = ResourceRole(role) if role is not None else None
    return tuple(
        resource
        for resource in RESOURCE_REGISTRY
        if (wanted_modality is None or wanted_modality in resource.modalities)
        and (wanted_role is None or wanted_role in resource.roles)
    )


def get_resource(resource_id: str) -> ModelResource:
    """Look up one resource by stable identifier."""

    for resource in RESOURCE_REGISTRY:
        if resource.resource_id == resource_id:
            return resource
    raise KeyError(resource_id)


def validate_registry(resources: Iterable[ModelResource] = RESOURCE_REGISTRY) -> None:
    """Raise ``ValueError`` when registry safety or completeness invariants fail."""

    entries = tuple(resources)
    ids = [entry.resource_id for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("resource_id values must be unique")
    for entry in entries:
        if not entry.roles or not entry.modalities or not entry.relevant_labels:
            raise ValueError(f"{entry.resource_id}: roles, modalities, and labels are required")
        if not entry.source_url.startswith("https://"):
            raise ValueError(f"{entry.resource_id}: source_url must use HTTPS")
        if not entry.limitations:
            raise ValueError(f"{entry.resource_id}: at least one limitation is required")
        unavailable = (
            ResourceRole.UNAVAILABLE in entry.roles
            or entry.support_status is SupportStatus.UNAVAILABLE
            or entry.artifact_availability is ArtifactAvailability.UNAVAILABLE
        )
        if entry.production_default and unavailable:
            raise ValueError(f"{entry.resource_id}: unavailable resource cannot be production default")
        if entry.production_default and entry.support_status is not SupportStatus.SUPPORTED:
            raise ValueError(f"{entry.resource_id}: production default must be locally supported")


validate_registry()


__all__ = [
    "ArtifactAvailability",
    "LicenseStatus",
    "Modality",
    "ModelResource",
    "REGISTRY_SCHEMA_VERSION",
    "RESOURCE_REGISTRY",
    "ResourceRole",
    "SupportStatus",
    "filter_resources",
    "get_resource",
    "validate_registry",
]
