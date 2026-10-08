"""Assess an exact user-defined instrument against supplied geometry only."""
import numpy as np

from skullbase_corridor.application.session import IntendedPath
from skullbase_corridor.domain.models import CorridorCase, SphereGeometry, VoxelGeometry
from skullbase_corridor.geometry.primitives import capsule_sphere_clearance, portal_allows_direction
from skullbase_corridor.geometry.voxel import VoxelMaskBackend


def assess_intended_path(case, path, base_directory=None):
    case = CorridorCase.model_validate(case.model_dump())
    path = IntendedPath.model_validate(path.model_dump())
    if case.coordinate_frame != "RAS":
        raise ValueError("Exact intended-path assessment requires a RAS-mm case")
    config = next((a for a in case.approaches if a.name == path.approach_name), None)
    if config is None:
        raise ValueError("Intended path references an approach absent from the case")
    entry, tip = np.asarray(path.entry_mm), np.asarray(path.tip_mm)
    delta = tip - entry
    depth = float(np.linalg.norm(delta))
    direction = delta / depth
    radius = max(config.instrument.radius_mm, config.instrument.tip_working_radius_mm)
    offset = entry - np.asarray(config.portal.center_mm)
    normal = np.asarray(config.portal.normal)
    in_plane = abs(float(offset @ normal)) <= 1e-5
    aperture = config.portal.radius_mm - float(np.linalg.norm(offset))
    portal_ok = in_plane and aperture > 0 and portal_allows_direction(
        direction, normal, aperture, radius)
    blockers = []
    unknown = []
    clearances = {}
    if not portal_ok:
        blockers.append("entry or shaft does not fit configured portal")
    if depth > config.instrument.length_mm:
        blockers.append("instrument length exceeded")
    if not case.protected_structures:
        unknown.append("no protected anatomy supplied")
    for structure in case.protected_structures:
        if structure.status.value != "known":
            unknown.append(structure.name)
            continue
        geometry = structure.geometry
        if isinstance(geometry, SphereGeometry):
            clearance = capsule_sphere_clearance(entry, tip, radius,
                                                  np.asarray(geometry.center_mm), geometry.radius_mm)
        elif isinstance(geometry, VoxelGeometry):
            # Deliberately no virtual removal: exact-path review is conservative.
            try:
                query = VoxelMaskBackend(geometry, base_directory=base_directory).capsule_clearance(
                    entry, tip, radius)
            except (ValueError, OSError, ImportError):
                unknown.append(f"{structure.name}: mask unavailable")
                continue
            if query.out_of_fov or query.clearance_mm is None:
                unknown.append(f"{structure.name}: outside assessed field")
                continue
            clearance = query.clearance_mm
        else:
            unknown.append(f"{structure.name}: unsupported geometry")
            continue
        clearances[structure.name] = float(clearance) if np.isfinite(clearance) else None
        if clearance <= 1e-9:
            blockers.append(structure.name)
    status = "blocked" if blockers else "unassessed" if unknown else "clear_of_supplied_geometry"
    return {
        "approach": path.approach_name, "status": status, "depth_mm": depth,
        "shaft_diameter_mm": 2 * config.instrument.radius_mm,
        "clearances_mm": clearances, "blockers": blockers, "unassessed_structures": unknown,
        "warning": "Only supplied geometry assessed; not surgical safety. "
                   "No virtual bone removal applied. Anatomy completeness requires expert review.",
    }
