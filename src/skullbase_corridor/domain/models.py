"""Versioned, affine-aware domain contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.1"
Vec3 = tuple[float, float, float]
Mat4 = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


def validate_physical_affine(value):
    array = np.asarray(value, dtype=float)
    if array.shape != (4, 4) or not np.all(np.isfinite(array)):
        raise ValueError("affine must be a finite 4x4 matrix")
    if not np.allclose(array[3], [0, 0, 0, 1], rtol=0, atol=1e-12):
        raise ValueError("affine must have homogeneous last row [0, 0, 0, 1]")
    if np.min(np.linalg.svd(array[:3, :3], compute_uv=False)) < 1e-12:
        raise ValueError("affine spatial transform must be invertible")
    return value


class KnowledgeStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    OUT_OF_FOV = "out_of_fov"


class AnalysisStatus(StrEnum):
    COMPLETE = "complete"
    ABSTAINED = "abstained"
    INCOMPLETE = "incomplete"
    UNSUPPORTED = "unsupported"
    NO_FEASIBLE_TRAJECTORY = "no_feasible_trajectory"


class ExactPathState(StrEnum):
    MODEL_FEASIBLE = "model_feasible"
    BLOCKED = "blocked"
    CONDITIONAL = "conditional"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class ApproachKind(StrEnum):
    EEA = "eea"
    TRANSMAXILLARY = "transmaxillary"


class CombinationMode(StrEnum):
    UNION = "union"
    INTERSECTION = "intersection"
    INCREMENTAL = "incremental"


class TargetPointCloud(StrictModel):
    kind: Literal["point_cloud"] = "point_cloud"
    points_mm: list[Vec3] = Field(min_length=1)
    point_volume_mm3: float | None = Field(default=None, gt=0)
    source: str = "explicit"
    coordinate_frame: Literal["RAS", "LPS"] = "RAS"

    def array(self) -> np.ndarray:
        return np.asarray(self.points_mm, dtype=float)


class SampledMaskTarget(TargetPointCloud):
    kind: Literal["sampled_mask"] = "sampled_mask"
    source: str = "mask_voxel_centers"
    affine: tuple[tuple[float, float, float, float], ...]
    source_shape: tuple[int, int, int]
    voxel_volume_mm3: float = Field(gt=0)

    @field_validator("affine")
    @classmethod
    def validate_affine(cls, value: tuple[tuple[float, float, float, float], ...]):
        return validate_physical_affine(value)

    @field_validator("source_shape")
    @classmethod
    def positive_shape(cls, value):
        if any(size <= 0 for size in value):
            raise ValueError("source shape dimensions must be positive")
        return value


class SphereGeometry(StrictModel):
    kind: Literal["sphere"] = "sphere"
    center_mm: Vec3
    radius_mm: float = Field(gt=0)


class VoxelGeometry(StrictModel):
    kind: Literal["voxel"] = "voxel"
    uri: str
    affine: tuple[tuple[float, float, float, float], ...]
    foreground_values: list[int] | None = None
    coordinate_frame: Literal["RAS", "LPS"] = "RAS"
    refine_near_boundary: bool = False
    max_refinement_cells: int = Field(default=128, ge=1)

    @field_validator("affine")
    @classmethod
    def validate_affine(cls, value: tuple[tuple[float, float, float, float], ...]):
        return validate_physical_affine(value)


class MeshGeometry(StrictModel):
    kind: Literal["mesh"] = "mesh"
    uri: str


ProtectedGeometry = Annotated[
    SphereGeometry | VoxelGeometry | MeshGeometry, Field(discriminator="kind")
]


class ProtectedStructure(StrictModel):
    name: str = Field(min_length=1)
    status: KnowledgeStatus = KnowledgeStatus.KNOWN
    geometry: ProtectedGeometry | None = None
    tissue_type: Literal["critical", "bone"] = "critical"
    allow_virtual_removal: bool = False

    @model_validator(mode="after")
    def known_requires_geometry(self):
        if self.status == KnowledgeStatus.KNOWN and self.geometry is None:
            raise ValueError("known protected structures require geometry")
        if self.allow_virtual_removal and self.tissue_type != "bone":
            raise ValueError("only bone structures may permit virtual removal")
        return self


class ImageReference(StrictModel):
    uri: str
    modality: Literal["CT", "MR", "synthetic"] = "CT"
    coordinate_frame: Literal["RAS", "LPS"] = "RAS"
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class PortalDisk(StrictModel):
    center_mm: Vec3
    normal: Vec3
    radius_mm: float = Field(gt=0)

    @field_validator("normal")
    @classmethod
    def normalize_normal(cls, value: Vec3) -> Vec3:
        vector = np.asarray(value, dtype=float)
        norm = np.linalg.norm(vector)
        if not np.isfinite(norm) or norm <= 1e-12:
            raise ValueError("portal normal must be finite and nonzero")
        if abs(norm - 1.0) <= 1e-12:
            return value
        return tuple(float(x) for x in vector / norm)  # type: ignore[return-value]


class RigidInstrument(StrictModel):
    length_mm: float = Field(gt=0)
    radius_mm: float = Field(ge=0)
    tip_working_radius_mm: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Radial working reach of the instrument tip; independent of shaft radius."
        ),
    )


class SamplingConfig(StrictModel):
    max_angle_deg: float = Field(default=35.0, gt=0, le=89.0)
    polar_steps: int = Field(default=9, ge=1)
    azimuth_steps: int = Field(default=48, ge=1)
    target_directed: bool = True
    adaptive_levels: int = Field(default=1, ge=0, le=4)
    max_target_witnesses: int = Field(default=256, ge=1)
    max_refinement_directions: int = Field(default=256, ge=0)
    portal_offset_rings: int = Field(default=0, ge=0, le=4)
    portal_offset_azimuth_steps: int = Field(default=8, ge=1, le=64)


class VirtualBoneRemoval(StrictModel):
    """An approach-specific, reviewed removal on a named, permitted bone grid."""

    structure_name: str = Field(min_length=1)
    geometry: VoxelGeometry
    reviewed: bool = False


class ApproachConfig(StrictModel):
    name: str = Field(min_length=1)
    kind: ApproachKind
    portal: PortalDisk
    nominal_direction: Vec3
    instrument: RigidInstrument
    sampling: SamplingConfig = SamplingConfig()
    target_tolerance_mm: float = Field(default=1.0, ge=0)
    shared_portal_overlap_mm: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Legacy unsupported exemption. Nonzero values disable pair analysis; "
            "proximal shaft collisions are never ignored."
        ),
    )
    allow_unknown_anatomy: bool = False
    virtual_bone_removals: list[VirtualBoneRemoval] = []

    @field_validator("nominal_direction")
    @classmethod
    def normalize_direction(cls, value: Vec3) -> Vec3:
        vector = np.asarray(value, dtype=float)
        norm = np.linalg.norm(vector)
        if not np.isfinite(norm) or norm <= 1e-12:
            raise ValueError("nominal direction must be finite and nonzero")
        if abs(norm - 1.0) <= 1e-12:
            return value
        return tuple(float(x) for x in vector / norm)  # type: ignore[return-value]


class CorridorCase(StrictModel):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    case_id: str
    coordinate_frame: Literal["RAS", "LPS"] = "RAS"
    source_image: ImageReference | None = None
    target: TargetPointCloud | SampledMaskTarget
    protected_structures: list[ProtectedStructure] = []
    approaches: list[ApproachConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_contracts(self):
        names = [approach.name for approach in self.approaches]
        if len(names) != len(set(names)):
            raise ValueError("approach names must be unique")
        structures = {s.name: s for s in self.protected_structures}
        if len(structures) != len(self.protected_structures):
            raise ValueError("protected structure names must be unique")
        framed = [self.target]
        if self.source_image is not None:
            framed.append(self.source_image)
        framed.extend(s.geometry for s in self.protected_structures
                      if isinstance(s.geometry, VoxelGeometry))
        for approach in self.approaches:
            removal_names = [r.structure_name for r in approach.virtual_bone_removals]
            if len(removal_names) != len(set(removal_names)):
                raise ValueError("duplicate removal for a bone structure")
            for removal in approach.virtual_bone_removals:
                structure = structures.get(removal.structure_name)
                if (structure is None or structure.tissue_type != "bone"
                        or not structure.allow_virtual_removal or not removal.reviewed
                        or structure.status != KnowledgeStatus.KNOWN
                        or not isinstance(structure.geometry, VoxelGeometry)):
                    raise ValueError("virtual removal requires reviewed, permitted, known voxel bone")
                framed.append(removal.geometry)
                if not np.allclose(removal.geometry.affine, structure.geometry.affine,
                                   rtol=0, atol=1e-6):
                    raise ValueError("virtual removal affine must match the bone affine")
        if any(item.coordinate_frame != self.coordinate_frame for item in framed):
            raise ValueError("all geometry must be explicitly converted to the case RAS/LPS frame")
        return self


class TrajectoryResult(StrictModel):
    direction: Vec3
    feasible: bool
    reason: str | None = None
    reached_point_indices: list[int] = []
    insertion_depths_mm: list[float] = []
    working_depth_mm: float | None = None
    insertion_span_mm: float | None = None
    entry_point_mm: Vec3 | None = None
    sampling_source: str = "angular_grid"
    conditional: bool = False
    minimum_clearance_mm: float | None = None


class ExactPathCandidate(StrictModel):
    """A finite straight insertion supplied by its exact world-space endpoints."""

    candidate_id: str = Field(min_length=1)
    approach_name: str = Field(min_length=1)
    entry_point_mm: Vec3
    target_point_mm: Vec3


class ExactPathResult(StrictModel):
    candidate_id: str
    approach_name: str
    state: ExactPathState
    entry_point_mm: Vec3
    target_point_mm: Vec3
    tip_point_mm: Vec3
    direction: Vec3 | None = None
    depth_mm: float | None = None
    minimum_clearance_mm: float | None = None
    reason: str | None = None
    aperture_satisfied: bool | None = None
    aperture_clearance_mm: float | None = None
    unknown_anatomy: list[str] = []
    required_removal_assumptions: list[str] = []


class ApproachResult(StrictModel):
    name: str
    kind: ApproachKind
    status: AnalysisStatus
    target_count: int
    reached_point_indices: list[int]
    reached_measure_mm3: float | None
    feasible_trajectory_count: int
    feasible_solid_angle_sr: float | None
    witness_direction: Vec3 | None
    witness_polar_angle_deg: float | None = None
    witness_azimuth_deg: float | None = None
    best_working_depth_mm: float | None
    minimum_clearance_mm: float | None
    best_insertion_span_mm: float | None = None
    witness_entry_point_mm: Vec3 | None = None
    sampling_complete: bool = False
    infeasibility_certified: bool = False
    trajectories: list[TrajectoryResult]
    notes: list[str] = []


class CombinationResult(StrictModel):
    first: str
    second: str
    union_indices: list[int]
    intersection_indices: list[int]
    incremental_first_indices: list[int]
    incremental_second_indices: list[int]
    status: AnalysisStatus = AnalysisStatus.COMPLETE
    notes: list[str] = []


class SimultaneousPairResult(StrictModel):
    first: str
    second: str
    feasible: bool | None
    minimum_angle_deg: float
    actual_angle_deg: float | None = None
    shaft_clearance_mm: float | None = None
    first_direction: Vec3 | None = None
    second_direction: Vec3 | None = None
    shared_portal_overlap_mm: float = 0.0
    first_reached_point_indices: list[int] = []
    second_reached_point_indices: list[int] = []
    status: AnalysisStatus = AnalysisStatus.COMPLETE
    first_entry_point_mm: Vec3 | None = None
    second_entry_point_mm: Vec3 | None = None
    notes: list[str] = []


class CaseResult(StrictModel):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    case_id: str
    approaches: list[ApproachResult]
    combinations: list[CombinationResult] = []
    simultaneous_pairs: list[SimultaneousPairResult] = []
