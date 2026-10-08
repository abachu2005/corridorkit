"""Deterministic analytical fixtures requiring no network data."""

from skullbase_corridor.domain.models import (
    ApproachConfig,
    ApproachKind,
    CorridorCase,
    PortalDisk,
    ProtectedStructure,
    RigidInstrument,
    SamplingConfig,
    SphereGeometry,
    TargetPointCloud,
)


def analytical_case() -> CorridorCase:
    target = TargetPointCloud(
        points_mm=[(0.0, 0.0, z) for z in (30.0, 40.0, 50.0)]
        + [(20.0, 0.0, 42.0)],
        point_volume_mm3=8.0,
    )
    common = dict(
        instrument=RigidInstrument(length_mm=80.0, radius_mm=1.0),
        sampling=SamplingConfig(max_angle_deg=30.0, polar_steps=7, azimuth_steps=36),
        target_tolerance_mm=2.0,
    )
    return CorridorCase(
        case_id="synthetic-analytical-v1",
        target=target,
        protected_structures=[
            ProtectedStructure(
                name="lateral-critical-sphere",
                geometry=SphereGeometry(center_mm=(10.0, -8.0, 35.0), radius_mm=3.0),
            )
        ],
        approaches=[
            ApproachConfig(
                name="EEA",
                kind=ApproachKind.EEA,
                portal=PortalDisk(center_mm=(0.0, 0.0, 0.0), normal=(0.0, 0.0, 1.0), radius_mm=5.0),
                nominal_direction=(0.0, 0.0, 1.0),
                **common,
            ),
            ApproachConfig(
                name="Transmaxillary",
                kind=ApproachKind.TRANSMAXILLARY,
                portal=PortalDisk(center_mm=(20.0, -20.0, 0.0), normal=(0.0, 0.4, 0.916515), radius_mm=5.0),
                nominal_direction=(0.0, 0.4, 0.916515),
                **common,
            ),
        ],
    )
