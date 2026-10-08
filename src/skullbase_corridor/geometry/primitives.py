"""Numerically robust analytic geometry in millimetres."""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]
EPS = 1e-10


def unit(vector: Array) -> Array:
    vector = np.asarray(vector, dtype=float)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError("vector must contain three finite coordinates")
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= EPS:
        raise ValueError("cannot normalize a zero vector")
    return vector / norm


def orthonormal_basis(axis: Array) -> tuple[Array, Array]:
    axis = unit(axis)
    helper = np.array([1.0, 0.0, 0.0])
    if abs(float(axis @ helper)) > 0.9:
        helper = np.array([0.0, 1.0, 0.0])
    first = unit(np.cross(axis, helper))
    return first, np.cross(axis, first)


def sample_spherical_cap(
    nominal_direction: Array,
    max_angle_deg: float,
    polar_steps: int,
    azimuth_steps: int,
) -> Array:
    """Deterministic equal-solid-angle rings, with the pole exactly once."""
    axis = unit(np.asarray(nominal_direction, dtype=float))
    if not math.isfinite(max_angle_deg) or not 0 < max_angle_deg < 90:
        raise ValueError("cap angle must be finite and between 0 and 90 degrees")
    if polar_steps < 1 or azimuth_steps < 1:
        raise ValueError("sampling steps must be positive")
    vectors = [axis]
    if polar_steps == 1:
        return np.asarray(vectors)
    u, v = orthonormal_basis(axis)
    cap_cos = math.cos(math.radians(max_angle_deg))
    for ring in range(1, polar_steps):
        fraction = ring / (polar_steps - 1)
        theta = math.acos(1.0 - fraction * (1.0 - cap_cos))
        for index in range(azimuth_steps):
            phi = 2.0 * math.pi * index / azimuth_steps
            direction = (
                math.cos(theta) * axis
                + math.sin(theta) * (math.cos(phi) * u + math.sin(phi) * v)
            )
            vectors.append(unit(direction))
    return np.asarray(vectors)


def spherical_cap_sample_weights(
    max_angle_deg: float,
    polar_steps: int,
    azimuth_steps: int,
) -> Array:
    """Voronoi-in-cos(theta) area weights matching ``sample_spherical_cap``.

    The pole is represented once while every other ring has ``azimuth_steps``
    samples, so an unweighted feasible fraction would over-weight the rings or
    pole depending on resolution. These weights sum to the exact cap area.
    """
    if not math.isfinite(max_angle_deg) or not 0 < max_angle_deg < 90:
        raise ValueError("cap angle must be finite and between 0 and 90 degrees")
    if polar_steps < 1 or azimuth_steps < 1:
        raise ValueError("sampling steps must be positive")
    cap_cos = math.cos(math.radians(max_angle_deg))
    cap_area = 2.0 * math.pi * (1.0 - cap_cos)
    if polar_steps == 1:
        return np.asarray([cap_area], dtype=float)
    levels = np.linspace(1.0, cap_cos, polar_steps)
    boundaries = np.empty(polar_steps + 1)
    boundaries[0] = 1.0
    boundaries[-1] = cap_cos
    boundaries[1:-1] = (levels[:-1] + levels[1:]) / 2.0
    ring_areas = 2.0 * math.pi * (boundaries[:-1] - boundaries[1:])
    weights = [ring_areas[0]]
    for area in ring_areas[1:]:
        weights.extend([area / azimuth_steps] * azimuth_steps)
    result = np.asarray(weights, dtype=float)
    if len(result) != 1 + (polar_steps - 1) * azimuth_steps:
        raise RuntimeError("sampling weights do not match spherical-cap layout")
    return result


def point_segment_distance(point: Array, start: Array, end: Array) -> float:
    delta = end - start
    squared = float(delta @ delta)
    if squared <= EPS:
        return float(np.linalg.norm(point - start))
    t = float(np.clip(((point - start) @ delta) / squared, 0.0, 1.0))
    return float(np.linalg.norm(point - (start + t * delta)))


def capsule_sphere_clearance(
    start: Array,
    end: Array,
    capsule_radius: float,
    sphere_center: Array,
    sphere_radius: float,
) -> float:
    """Signed surface clearance; zero is tangency and negative is collision."""
    return point_segment_distance(sphere_center, start, end) - capsule_radius - sphere_radius


def segment_segment_distance(p1: Array, q1: Array, p2: Array, q2: Array) -> float:
    """Shortest distance between finite 3-D segments (Ericson algorithm)."""
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a, e, f = float(d1 @ d1), float(d2 @ d2), float(d2 @ r)
    if a <= EPS and e <= EPS:
        return float(np.linalg.norm(p1 - p2))
    if a <= EPS:
        s, t = 0.0, float(np.clip(f / e, 0.0, 1.0))
    else:
        c = float(d1 @ r)
        if e <= EPS:
            t, s = 0.0, float(np.clip(-c / a, 0.0, 1.0))
        else:
            b = float(d1 @ d2)
            denominator = a * e - b * b
            s = float(np.clip((b * f - c * e) / denominator, 0.0, 1.0)) if denominator > EPS else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, float(np.clip(-c / a, 0.0, 1.0))
            elif t > 1.0:
                t, s = 1.0, float(np.clip((b - c) / a, 0.0, 1.0))
    return float(np.linalg.norm((p1 + s * d1) - (p2 + t * d2)))


def capsule_capsule_clearance(
    p1: Array, q1: Array, radius1: float, p2: Array, q2: Array, radius2: float
) -> float:
    return segment_segment_distance(p1, q1, p2, q2) - radius1 - radius2


def angle_degrees(first: Array, second: Array) -> float:
    cosine = float(np.clip(unit(first) @ unit(second), -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def portal_allows_direction(
    direction: Array, portal_normal: Array, portal_radius: float, shaft_radius: float
) -> bool:
    """Require a circular shaft's oblique plane footprint to fit in the disk."""
    if (not math.isfinite(portal_radius) or not math.isfinite(shaft_radius)
            or shaft_radius < 0):
        raise ValueError("portal and nonnegative shaft radius must be finite")
    usable = portal_radius - shaft_radius
    if usable < -EPS:
        return False
    cosine = float(unit(direction) @ unit(portal_normal))
    if cosine <= EPS:
        return False
    # Oblique cylinder/disk intersection has semi-major radius r / cos(theta).
    return shaft_radius / cosine <= portal_radius + EPS
