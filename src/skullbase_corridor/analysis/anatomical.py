"""Explicit, NumPy-only landmark measurements; not a corridor safety analysis.

All coordinates are supplied by the caller in one world RAS frame, in millimetres.
No anatomical landmarks, lateral limits, or clinical coordinates are inferred.
"""

from __future__ import annotations

import numpy as np

__all__ = ["compare_landmarks"]


def _triple(value, name: str) -> np.ndarray:
    """Reject unknown, non-real, nonfinite, and incorrectly shaped coordinates."""
    try:
        raw = np.asarray(value)
        if raw.shape != (3,) or raw.dtype.kind not in "iuf":
            raise ValueError
        with np.errstate(over="ignore", invalid="ignore"):
            vector = raw.astype(float)
        if not np.all(np.isfinite(vector)):
            raise ValueError
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a triple of finite real numbers") from exc
    return vector


def _unit(vector: np.ndarray, name: str) -> np.ndarray:
    # Scale first so nonzero normals/axes need not have any particular magnitude.
    scale = float(np.max(np.abs(vector)))
    if scale == 0:
        raise ValueError(f"{name} must be nonzero")
    scaled = vector / scale
    return scaled / np.linalg.norm(scaled)


def _difference(end: np.ndarray, start: np.ndarray, name: str) -> np.ndarray:
    with np.errstate(over="ignore", invalid="ignore"):
        result = end - start
    if not np.all(np.isfinite(result)):
        raise ValueError(f"{name} exceeds the finite numeric range")
    return result


def _projected_unit(vector: np.ndarray, normal: np.ndarray, name: str) -> np.ndarray:
    direction = _unit(vector, name)
    projected = direction - np.dot(direction, normal) * normal
    # A relative tolerance detects cancellation when an axis is parallel to n,
    # including after a rigid rotation; it does not impose a length unit cutoff.
    if np.linalg.norm(projected) <= 64 * np.finfo(float).eps:
        raise ValueError(f"{name} collapses in the measurement plane")
    return _unit(projected, name)


def _distance(vector: np.ndarray, name: str) -> float:
    with np.errstate(over="ignore", invalid="ignore"):
        distance = float(np.hypot.reduce(vector))
    if not np.isfinite(distance):
        raise ValueError(f"{name} exceeds the finite numeric range")
    return distance


def compare_landmarks(
    eea_entry,
    ctm_entry,
    target,
    ica_start,
    ica_end,
    *,
    plane_normal=(0, 0, 1),
    eea_limit=None,
    ctm_limit=None,
    lateral_axis=None,
) -> dict:
    """Compare EEA and contralateral transmaxillary (CTM) landmark geometry.

    Points must be finite real triples in world RAS mm. ``plane_normal`` and
    ``lateral_axis`` are direction triples in the same frame, normalized here.
    The default normal defines the axial (RAS XY) plane. Both approaches use
    exactly the same target and the same ICA line.

    Angles are the acute undirected projected angles (inclusive range 0–90
    degrees), so reversing ICA endpoints cannot change them. Advantage is
    EEA minus CTM; positive means a smaller CTM angle, not demonstrated benefit.
    Working distance is the full 3-D Euclidean entry-to-target distance.

    Supply all three optional lateral inputs or none. Additional lateral reach
    is the signed scalar ``dot(ctm_limit - eea_limit, unit(lateral_axis))``,
    not the distance between limits and not computed reachable anatomy.

    Raises ValueError for invalid triples, zero normals/axes, collapsed
    projected vectors (within 64 machine eps of parallel after normalization),
    partial lateral inputs, or numerically unrepresentable measurements.
    Unknown keyword arguments raise TypeError. Returns only JSON-safe values.
    """
    optional = (eea_limit, ctm_limit, lateral_axis)
    if any(value is not None for value in optional) and not all(
        value is not None for value in optional
    ):
        raise ValueError("eea_limit, ctm_limit, and lateral_axis must be supplied together")

    points = {
        "eea_entry": _triple(eea_entry, "eea_entry"),
        "ctm_entry": _triple(ctm_entry, "ctm_entry"),
        "target": _triple(target, "target"),
        "ica_start": _triple(ica_start, "ica_start"),
        "ica_end": _triple(ica_end, "ica_end"),
    }
    raw_normal = _triple(plane_normal, "plane_normal")
    normal = _unit(raw_normal, "plane_normal")
    ica = _difference(points["ica_end"], points["ica_start"], "ICA axis")
    projected_ica = _projected_unit(ica, normal, "ICA axis")
    measurements = {}
    for approach in ("eea", "ctm"):
        direction = _difference(
            points["target"], points[f"{approach}_entry"], f"{approach} entry-to-target"
        )
        projected = _projected_unit(direction, normal, f"{approach} entry-to-target")
        # atan2 is well-conditioned at both zero and ninety degrees; abs(dot)
        # makes the angle undirected, independently of ICA endpoint order.
        angle = np.degrees(
            np.arctan2(np.linalg.norm(np.cross(projected, projected_ica)),
                       abs(float(np.dot(projected, projected_ica))))
        )
        measurements[f"{approach}_angle_deg"] = float(angle)
        measurements[f"{approach}_working_distance_mm"] = _distance(
            direction, f"{approach} working distance"
        )
    measurements["angle_advantage_deg"] = (
        measurements["eea_angle_deg"] - measurements["ctm_angle_deg"]
    )

    reach = None
    if eea_limit is not None:
        points["eea_limit"] = _triple(eea_limit, "eea_limit")
        points["ctm_limit"] = _triple(ctm_limit, "ctm_limit")
        raw_axis = _triple(lateral_axis, "lateral_axis")
        axis = _unit(raw_axis, "lateral_axis")
        delta = _difference(points["ctm_limit"], points["eea_limit"], "lateral limits")
        with np.errstate(over="ignore", invalid="ignore"):
            reach = float(np.dot(delta, axis))
        if not np.isfinite(reach):
            raise ValueError("additional lateral reach exceeds the finite numeric range")
    else:
        raw_axis = None

    return {
        "coordinate_system": "world RAS",
        "coordinate_units": "mm",
        "measurement_type": "anatomical_projected_landmark_comparison",
        "inputs": {
            **{name: point.tolist() for name, point in points.items()},
            "plane_normal": raw_normal.tolist(),
            "eea_limit": None if eea_limit is None else points["eea_limit"].tolist(),
            "ctm_limit": None if ctm_limit is None else points["ctm_limit"].tolist(),
            "lateral_axis": None if raw_axis is None else raw_axis.tolist(),
        },
        **measurements,
        "additional_lateral_reach_mm": reach,
        "definitions": {
            "projection": "P(v) = v - dot(v, unit(plane_normal)) * unit(plane_normal).",
            "angles": (
                "Acute undirected anatomical projected angle in degrees, 0 through 90: "
                "acos(abs(dot(unit(P(target-entry)), unit(P(ica_end-ica_start))))). "
                "Both entries share the same target; ICA endpoint order is irrelevant."
            ),
            "angle_advantage_deg": (
                "eea_angle_deg - ctm_angle_deg; positive means a smaller CTM angle, "
                "not established clinical superiority."
            ),
            "working_distances": "3-D Euclidean norm(target-entry), in mm; not projected.",
            "additional_lateral_reach_mm": (
                "dot(ctm_limit-eea_limit, unit(lateral_axis)), in mm; positive is along "
                "the caller's signed lateral axis. Null means limits were not supplied."
            ),
        },
        "limitations": [
            "The angle is an anatomical projected measurement, not a safe path.",
            "No collision, ICA clearance, instrument, access, or reachability is computed.",
            "Optional lateral limits are user defined, not computed reachability.",
            "Working distances are straight-line landmark distances, not surgical paths.",
            "Results depend on supplied landmarks and measurement plane; a two-point "
            "ICA axis does not model vessel curvature or diameter.",
            "Inputs must share one world RAS mm frame; registration, anatomical identity, "
            "and clinical validity are not verified. No clinical coordinates are inferred.",
        ],
    }
