"""Geometry-aware center-entry solid angle for unobstructed point targets.

The feasible set is a union of spherical axial bands intersected with the portal
and sampling caps. At each equal-area latitude u=1-cos(theta), solve azimuth
intervals analytically, union them, and integrate their length in u. All boundary
circle extrema and intersections split the integration domain BEFORE quadrature.
Thus narrow components are not discovered by Boolean sampling.

QUADPACK's error estimate is not a certified bound. This module does not model
protected structures and must never be used to bypass collision/FOV checks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from time import perf_counter
from typing import Callable

import numpy as np
from scipy.integrate import quad

from skullbase_corridor.geometry.primitives import orthonormal_basis, unit


@dataclass(frozen=True)
class GeometricAngularResult:
    solid_angle_sr: float | None
    estimated_error_sr: float | None
    evaluations: int
    interval_count: int
    elapsed_seconds: float
    termination_reason: str
    error_is_certified: bool = False


class _BudgetExceeded(Exception):
    pass


def _intersect(first, second):
    return [(max(a, c), min(b, d)) for a, b in first for c, d in second
            if max(a, c) < min(b, d)]


def _azimuth_intervals(normal, threshold, u):
    """Solve n.d >= c, with normal in the integration frame."""
    amplitude = math.sqrt(max(0., u * (2 - u))) * math.hypot(normal[0], normal[1])
    residual = threshold - normal[2] + u * normal[2]
    if amplitude == 0:
        return [(0., 2 * math.pi)] if residual <= 0 else []
    ratio = residual / amplitude
    if ratio <= -1:
        return [(0., 2 * math.pi)]
    if ratio >= 1:
        return []
    center = math.atan2(normal[1], normal[0]) % (2 * math.pi)
    width = math.acos(ratio)
    lo, hi = center - width, center + width
    if lo < 0:
        return [(0., hi), (lo + 2 * math.pi, 2 * math.pi)]
    if hi > 2 * math.pi:
        return [(0., hi - 2 * math.pi), (lo, 2 * math.pi)]
    return [(lo, hi)]


def _breakpoints(constraints, cap_u):
    """Latitude extrema and pairwise intersections of all boundary circles."""
    points = [0., cap_u]
    active = [(n, c) for n, c in constraints if -1 < c < 1]
    for normal, c in active:
        spread = math.sqrt(max(0., (1 - c) * (1 + c))) * math.hypot(*normal[:2])
        points.extend((1 - c * normal[2] - spread, 1 - c * normal[2] + spread))
    for i, (n, c) in enumerate(active):
        for m, d in active[:i]:
            cross = np.cross(n, m)
            squared = float(cross @ cross)
            if squared < 1e-28:
                # Parallel/coincident circles have no isolated crossing.
                continue
            # Minimum-norm solution of n.x=c and m.x=d.
            base = (c * np.cross(m, cross) + d * np.cross(cross, n)) / squared
            remainder = 1 - float(base @ base)
            if remainder >= 0:
                dz = math.sqrt(remainder / squared) * cross[2]
                points.extend((1 - base[2] - dz, 1 - base[2] + dz))
    return sorted(set(p for p in points if 0 <= p <= cap_u))


def integrate_unobstructed_solid_angle(
    target_vectors,
    *,
    axis,
    half_angle_deg,
    portal_normal,
    portal_radius_mm,
    collision_radius_mm,
    reach_radius_mm,
    instrument_length_mm,
    geometric_tolerance_mm=1e-9,
    relative_tolerance=1e-5,
    absolute_tolerance_sr=1e-12,
    max_evaluations=100_000,
    max_seconds=5.,
    cancel_check: Callable[[], None] | None = None,
) -> GeometricAngularResult:
    """Integrate declared geometry; caller must establish absence of obstacles.

    Finite length produces a band, not a target cap: axial depth must not exceed
    instrument length. Duplicate/overlapping target bands are unioned, not added.
    Up to 32 target points are supported to bound boundary-arrangement work.
    Failure/budget exhaustion returns no measurement, never partial area or zero.
    Cancellation exceptions propagate.
    """
    started = perf_counter()
    targets = np.asarray(target_vectors, dtype=float)
    if targets.ndim != 2 or targets.shape[1:] != (3,) or not len(targets):
        raise ValueError("target_vectors must be a nonempty Nx3 array")
    if not np.all(np.isfinite(targets)):
        raise ValueError("target_vectors must be finite")
    positive = (portal_radius_mm, instrument_length_mm, relative_tolerance,
                absolute_tolerance_sr, max_seconds)
    nonnegative = (collision_radius_mm, reach_radius_mm, geometric_tolerance_mm)
    if any(not math.isfinite(v) or v <= 0 for v in positive):
        raise ValueError("radii, length, tolerances and deadline must be positive finite")
    if any(not math.isfinite(v) or v < 0 for v in nonnegative):
        raise ValueError("reach/collision radii and geometry tolerance must be nonnegative finite")
    if not math.isfinite(half_angle_deg) or not 0 < half_angle_deg < 90:
        raise ValueError("half_angle_deg must lie between 0 and 90")
    if isinstance(max_evaluations, bool) or not isinstance(max_evaluations, int) or max_evaluations < 1:
        raise ValueError("max_evaluations must be a positive integer")
    nominal = unit(axis)
    first, second = orthonormal_basis(nominal)
    frame = np.asarray([first, second, nominal])
    portal = frame @ unit(portal_normal)
    cap_u = 2 * math.sin(math.radians(half_angle_deg) / 2) ** 2
    evaluations = 0
    interval_count = 0

    def result(area, error, reason):
        return GeometricAngularResult(area, error, evaluations, interval_count,
                                      perf_counter() - started, reason)

    def check():
        if cancel_check is not None:
            cancel_check()
        if perf_counter() - started >= max_seconds:
            raise _BudgetExceeded("time_budget")

    if len(targets) > 32:
        return result(None, None, "target_limit")
    # Match portal_allows_direction's physical tolerance and front-side test.
    portal_threshold = max(1e-10, collision_radius_mm / (portal_radius_mm + 1e-10))
    if portal_threshold >= 1 or portal_radius_mm - collision_radius_mm < -1e-10:
        return result(0., 0., "geometry_empty")
    common = (portal, portal_threshold)
    bands = []
    constraints = [common]
    for vector in targets:
        distance = float(np.linalg.norm(vector))
        if not math.isfinite(distance):
            raise ValueError("target distances must be finite")
        if distance == 0:
            bands = [[]]  # Entry target is reached at zero insertion for all directions.
            break
        direction = frame @ (vector / distance)
        radius = reach_radius_mm + geometric_tolerance_mm
        if radius < distance:
            # Stable cap deficit; avoid subtracting squared large distances.
            ratio = radius / distance
            lower = math.sqrt((1 - ratio) * (1 + ratio))
        else:
            lower = max(-1., -geometric_tolerance_mm / distance)
        upper = min(1., (instrument_length_mm + geometric_tolerance_mm) / distance)
        if lower >= upper:
            continue
        band = [(direction, lower), (-direction, -upper)]
        bands.append(band)
        constraints.extend(band)
    if not bands:
        return result(0., 0., "geometry_empty")
    try:
        check()
        splits = _breakpoints(constraints, cap_u)
        check()
        interval_count = len(splits) - 1

        def width(u):
            nonlocal evaluations
            check()
            if evaluations >= max_evaluations:
                raise _BudgetExceeded("evaluation_budget")
            evaluations += 1
            allowed = _azimuth_intervals(*common, u)
            intervals = []
            for band in bands:
                current = allowed
                for normal, c in band:
                    current = _intersect(current, _azimuth_intervals(normal, c, u))
                    if not current:
                        break
                intervals.extend(current)
            end = total = 0.
            for lo, hi in sorted(intervals):
                total += max(0., hi - max(lo, end))
                end = max(end, hi)
            return total

        areas, errors = [], []
        for lo, hi in zip(splits, splits[1:]):
            answer = quad(width, lo, hi, epsabs=absolute_tolerance_sr / interval_count,
                          epsrel=relative_tolerance, limit=150, full_output=1)
            if len(answer) != 3:
                return result(None, None, "quadrature_failure")
            areas.append(answer[0])
            errors.append(answer[1])
        area, error = math.fsum(areas), math.fsum(errors)
        if error > max(absolute_tolerance_sr, relative_tolerance * area):
            return result(None, error, "estimated_tolerance_not_met")
        return result(area, error, "estimated_tolerance")
    except _BudgetExceeded as exc:
        return result(None, None, str(exc))
