#!/usr/bin/env python3
"""Research-only area brackets; no production imports/changes.

Exact-arithmetic enclosure derivation, NOT a floating-point certificate.
Prototype supports one target, spheres and closed axis-aligned boxes, not
general affine voxel masks or the production conservative predicate.
"""

import argparse
import hashlib
import heapq
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GUARD = 1e-11  # engineering guard, NOT a directed-rounding proof


def unit_tilt(degrees):
    t = math.radians(degrees)
    return np.array([math.sin(t), 0., math.cos(t)])


def cap_area(degrees):
    return 4 * math.pi * math.sin(math.radians(degrees) / 2) ** 2


def box_segment_distance(endpoint, lo, hi):
    """Exact-arithmetic piecewise quadratic minimization on the segment."""
    events = [0., 1.]
    for axis in range(3):
        if endpoint[axis] != 0:
            events.extend(x / endpoint[axis] for x in (lo[axis], hi[axis])
                          if 0 < x / endpoint[axis] < 1)
    events = sorted(set(events))
    best = math.inf
    witness = None
    for start, stop in zip(events, events[1:]):
        mid = (start + stop) / 2
        point = endpoint * mid
        outside = (point < lo) | (point > hi)
        if not np.any(outside):
            # Interior point provides a signed penetration witness even for r=0.
            penetration = float(np.min(np.minimum(point - lo, hi - point)))
            if witness is None or -penetration < witness:
                witness = -penetration
            best = 0.
            continue
        boundary = np.where(point < lo, lo, hi)[outside]
        velocity = endpoint[outside]
        norm = float(velocity @ velocity)
        candidate = mid if norm == 0 else float(np.clip(
            (velocity @ boundary) / norm, start, stop))
        query = endpoint * candidate
        distance = float(np.linalg.norm(query - np.clip(query, lo, hi)))
        best = min(best, distance)
    return best, best if witness is None else witness


def classify(cell, problem):
    """Return 1=entirely feasible, -1=entirely blocked, 0=unresolved."""
    t0, t1, p0, p1 = cell
    theta, phi = (t0 + t1) / 2, (p0 + p1) / 2
    center = np.array([math.sin(theta) * math.cos(phi),
                       math.sin(theta) * math.sin(phi), math.cos(theta)])
    # A meridian + parallel path bounds geodesic distance to every cell point.
    rho = min(math.pi, (t1 - t0) / 2 + math.sin(t1) * (p1 - p0) / 2 + GUARD)
    chord = 2 * math.sin(rho / 2)
    target = np.asarray(problem["target"])
    distance = float(np.linalg.norm(target))
    target_axis = target / distance
    offset = math.acos(float(np.clip(target_axis @ center, -1, 1)))
    lower_dot = math.cos(min(math.pi, offset + rho)) - GUARD
    upper_dot = math.cos(max(0., offset - rho)) + GUARD
    ratio = problem["reach"] / distance
    cap_cos = math.sqrt(max(0., (1 - ratio) * (1 + ratio))) if ratio < 1 else 0.
    max_cos = min(1., problem["length"] / distance)
    if upper_dot < cap_cos or lower_dot > max_cos:
        return -1
    geometric_clear = lower_dot >= cap_cos and upper_dot <= max_cos + 2 * GUARD
    axial = max(0., float(target @ center))
    endpoint = center * axial
    # E(d) = max(0,v.d)d; |E(d)-E(c)| <= (D+depth(c))*|d-c|.
    displacement = (distance + axial) * chord + GUARD
    obstacle_clear = True
    radius = problem["radius"]
    for xyz, sphere_radius in problem.get("spheres", []):
        xyz = np.asarray(xyz)
        fraction = 0. if axial == 0 else float(np.clip((xyz @ center) / axial, 0., 1.))
        clearance = float(np.linalg.norm(xyz - fraction * endpoint)) - sphere_radius - radius
        if clearance + displacement < -GUARD:
            return -1
        obstacle_clear &= clearance - displacement > GUARD
    for lo, hi in problem.get("boxes", []):
        distance_to_box, signed_witness = box_segment_distance(
            endpoint, np.asarray(lo), np.asarray(hi))
        if signed_witness - radius + displacement < -GUARD:
            return -1
        obstacle_clear &= distance_to_box - radius - displacement > GUARD
    # Out-of-FOV cells remain unresolved; not counted as physically blocked.
    fov_clear = True
    if "fov" in problem:
        lo, hi = np.asarray(problem["fov"][0]), np.asarray(problem["fov"][1])
        fov_clear = bool(np.all(lo + radius <= 0) and np.all(hi - radius >= 0)
                         and np.all(endpoint - displacement >= lo + radius)
                         and np.all(endpoint + displacement <= hi - radius))
    return 1 if geometric_clear and obstacle_clear and fov_clear else 0


def integrate(problem, budget, max_seconds=20.):
    started = perf_counter()
    pending = []
    lower = unresolved = 0.
    calls = serial = 0

    def add(cell):
        nonlocal lower, unresolved, calls, serial
        t0, t1, p0, p1 = cell
        # Stable exact-area identity, avoids cancellation at narrow polar caps.
        area = 2 * math.sin((t1 + t0) / 2) * math.sin((t1 - t0) / 2) * (p1 - p0)
        status = classify(cell, problem)
        calls += 1
        if status == 1:
            lower += area
        elif status == 0:
            unresolved += area
            serial += 1
            heapq.heappush(pending, (-area, serial, cell))

    theta_max = math.radians(problem.get("domain_deg", 45))
    for j in range(16):
        add((0., theta_max, 2 * math.pi * j / 16, 2 * math.pi * (j + 1) / 16))
    reason = "evaluation_budget"
    while pending:
        # midpoint error <= (U-L)/2, relative to actual area >= L.
        if lower > 0 and unresolved / (2 * lower) <= .01:
            reason = "relative_bracket_target"
            break
        if calls + 2 > budget:
            break
        if perf_counter() - started > max_seconds:
            reason = "time_budget"
            break
        negative_area, _, cell = heapq.heappop(pending)
        unresolved += negative_area
        t0, t1, p0, p1 = cell
        if t1 - t0 >= math.sin(t1) * (p1 - p0):
            mid = (t0 + t1) / 2
            add((t0, mid, p0, p1))
            add((mid, t1, p0, p1))
        else:
            mid = (p0 + p1) / 2
            add((t0, t1, p0, mid))
            add((t0, t1, mid, p1))
    if not pending:
        reason = "all_cells_classified"
    unresolved = max(0., unresolved)
    upper = lower + unresolved
    expected = problem.get("expected")
    midpoint = (lower + upper) / 2
    return {
        "lower_sr": lower, "upper_sr": upper, "unresolved_area_sr": unresolved,
        "midpoint_sr": midpoint,
        "worst_case_midpoint_relative_error_bound_exact_arithmetic":
            None if lower == 0 else unresolved / (2 * lower),
        "relative_target_met_exact_arithmetic": lower > 0 and unresolved / (2 * lower) <= .01,
        "evaluations": calls, "pending_cells": len(pending),
        "runtime_seconds": perf_counter() - started, "termination": reason,
        "reference_inside_bracket": None if expected is None else lower <= expected <= upper,
        "midpoint_actual_relative_error": None if expected is None else abs(midpoint - expected) / expected,
        "error_is_certified": False,
    }


def cases():
    base = {"target": [0., 0., 10.], "reach": 20., "length": 20., "radius": .1}
    for tilt in (0., 19.):
        expected = cap_area(45) - cap_area(math.degrees(math.asin(1.1 / 5)))
        yield dict(base, name=f"sphere_shadow_{tilt}", spheres=[
            [(5 * unit_tilt(tilt)).tolist(), 1.]], expected=expected)
    yield dict(base, name="sphere_beyond_finite_tip", spheres=[[[0, 0, 15], 1.]],
               expected=cap_area(45))
    # Independent rectangular pyramid solid angle for a closed cube, ray radius=0.
    box_area = 4 * math.atan(.25 / (4.5 * math.sqrt(4.5 ** 2 + .5)))
    yield dict(base, name="closed_voxel_shadow_ray", radius=0.,
               boxes=[[[-.5, -.5, 4.5], [.5, .5, 5.5]]],
               expected=cap_area(45) - box_area)
    yield dict(base, name="closed_voxel_shadow_finite_shaft", radius=.1,
               boxes=[[[-.5, -.5, 4.5], [.5, .5, 5.5]]])
    yield dict(base, name="narrow_target_with_distant_protection", reach=10 * math.sin(math.radians(.1)),
               spheres=[[[5, 0, 5], 1.]], expected=cap_area(.1))
    yield dict(base, name="thin_protected_annulus", radius=.0001,
               reach=10 * math.sin(math.radians(.1)),
               spheres=[[[0, 0, 5], 5 * math.sin(math.radians(.0995)) - .0001]],
               expected=cap_area(.1) - cap_area(.0995))
    yield dict(base, name="unknown_fov_not_infeasible",
               fov=[[-3., -3., -1.], [3., 3., 6.]], expected=cap_area(45))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "research/protected-angular-prototype/results.json")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite prior prototype results")
    source_paths = sorted((ROOT / "src").rglob("*.py"))
    before = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source_paths}
    rows = []
    for problem in cases():
        for budget in (4000, 40000):
            result = integrate(problem, budget)
            rows.append({"problem": problem, "budget": budget, "result": result})
            print(json.dumps({"name": problem["name"], "budget": budget, **result}), flush=True)
    after = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in source_paths}
    output = {
        "scope": "Research prototype: one target, analytic spheres and axis-aligned closed boxes.",
        "limitations": [
            "Exact-arithmetic area-enclosure argument; floating-point guards not certified.",
            "No affine voxel tree or production predicate integration.",
            "A passing midpoint error alone does not count as a passing bracket.",
            "Unknown FOV stays unresolved even where no valid known-area lower bound exists.",
            "Budget and 1% bracket criterion fixed before first execution.",
        ],
        "budgets": [4000, 40000], "relative_tolerance": .01,
        "rows": rows, "production_source_hashes_before": before,
        "production_source_hashes_after": after, "production_sources_unchanged": before == after,
        "prototype_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
