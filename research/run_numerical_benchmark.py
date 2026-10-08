#!/usr/bin/env python3
"""Bounded synthetic numerical evidence; run with existing Python, no downloads.

Example: PYTHONPATH=src python research/run_numerical_benchmark.py
Writes only research/numerical-benchmark by default. Not clinical validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import numpy as np
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from corridorkit.analysis.baselines import (
    analytic_cap_solid_angle, off_axis_target_solid_angle,
    optimized_capsule_sphere_clearance, parameter_hash, run_engine_ablations,
)
from corridorkit.domain.models import CorridorCase, VoxelGeometry
from corridorkit.geometry.engine import analyze_case
from corridorkit.geometry.mesh_reference import VoxelCellReference
from corridorkit.geometry.primitives import capsule_sphere_clearance
from corridorkit.geometry.voxel import VoxelMaskBackend

SEED = 20261001


def hashes():
    paths = [
        Path(__file__).resolve(),
        *(ROOT / "src/corridorkit" / path for path in (
            "analysis/baselines.py", "geometry/mesh_reference.py", "geometry/engine.py",
            "geometry/voxel.py", "geometry/primitives.py", "domain/models.py")),
        ROOT / "tests/test_baselines.py", ROOT / "tests/test_mesh_reference.py",
    ]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def approach(name="A", center=(0, 0, 0), direction=(0, 0, 1), radius=.5):
    return {
        "name": name, "kind": "eea",
        "portal": {"center_mm": center, "normal": direction, "radius_mm": 2},
        "nominal_direction": direction,
        "instrument": {"length_mm": 20, "radius_mm": radius},
        "sampling": {"max_angle_deg": 25, "polar_steps": 5, "azimuth_steps": 16,
                     "target_directed": True, "adaptive_levels": 0},
        "target_tolerance_mm": .01,
    }


def phantoms(output):
    cases = [
        {"case_id": "unobstructed", "target": {"points_mm": [[2, 0, 10]]},
         "approaches": [approach()]},
        {"case_id": "side_obstacle", "target": {"points_mm": [[2, 0, 10]]},
         "approaches": [approach()], "protected_structures": [
             {"name": "synthetic_sphere", "geometry": {
                 "kind": "sphere", "center_mm": [1.5, 0, 5], "radius_mm": .2}}]},
        {"case_id": "shared_portal", "target": {"points_mm": [[-2, 0, 10], [2, 0, 10]]},
         "approaches": [approach("A"), approach("B")]},
        # Separate entries and outward-directed targets: no common-base exemption.
        {"case_id": "disjoint_offset_portals",
         "target": {"points_mm": [[-5, 0, 10], [5, 0, 10]]},
         "approaches": [approach("A", (-3, 0, 0), (-.2, 0, 1)),
                        approach("B", (3, 0, 0), (.2, 0, 1))]},
        {"case_id": "beyond_finite_length", "target": {"points_mm": [[0, 0, 25]]},
         "approaches": [approach()]},
    ]
    path = output / "empty_fov_mask.npy"
    np.save(path, np.zeros((9, 9, 9), dtype=np.uint8))
    affine = np.eye(4)
    affine[:3, 3] = [-4, -4, -2]
    cases.append({
        "case_id": "out_of_fov", "target": {"points_mm": [[0, 0, 10]]},
        "approaches": [approach("A"), approach("B")],
        "protected_structures": [{"name": "synthetic_empty_limited_fov", "geometry": {
            "kind": "voxel", "uri": str(path), "affine": affine.tolist()}}],
    })
    return [CorridorCase.model_validate(case) for case in cases]


def ablation_evidence(output):
    rows = []
    for case in phantoms(output):
        for row in run_engine_ablations(case):
            row["case_id"] = case.case_id
            if row["result"] is not None:
                for result in row["result"]["approaches"]:
                    samples = result.pop("trajectories")
                    result["evaluated_sample_count"] = len(samples)
                    result["sampling_hash"] = parameter_hash([
                        [sample["direction"], sample["entry_point_mm"], sample["sampling_source"]]
                        for sample in samples])
                    result["reason_counts"] = {
                        reason: sum(sample["reason"] == reason for sample in samples)
                        for reason in sorted({s["reason"] for s in samples if s["reason"]})
                    }
            rows.append(row)
    return rows


def sphere_evidence(rng):
    started = perf_counter()
    rows = []
    for index in range(128):
        start, end, center = rng.normal(size=(3, 3)) * 10
        first, second = rng.uniform(0, 3, 2)
        if index == 0:
            end = start.copy()
        expected = optimized_capsule_sphere_clearance(start, end, first, center, second)
        actual = capsule_sphere_clearance(start, end, first, center, second)
        rows.append({"index": index, "absolute_error_mm": abs(actual - expected),
                     "production_clearance_mm": actual, "reference_clearance_mm": expected})
    return {"reference": "independent scipy bounded scalar quadratic minimization",
            "rows": rows, "max_absolute_error_mm": max(r["absolute_error_mm"] for r in rows),
            "runtime_seconds": perf_counter() - started}


def mesh_evidence(output, rng):
    started = perf_counter()
    mask = np.zeros((9, 9, 9), dtype=np.uint8)
    mask[4, 4, 4] = 1
    path = output / "single_cell_mask.npy"
    np.save(path, mask)
    affine = np.eye(4)
    affine[:3, :3] = [[1, .4, .1], [.2, 1.3, .2], [0, .1, .9]]
    reference = VoxelCellReference(np.argwhere(mask), affine)
    backend = VoxelMaskBackend(VoxelGeometry(uri=str(path), affine=affine.tolist()))
    rows = []
    center = affine[:3, :3] @ np.array([4, 4, 4])
    normal = np.linalg.inv(affine[:3, :3]).T[:, 0]
    normal /= np.linalg.norm(normal)
    face = center + affine[:3, :3] @ np.array([.5, 0, 0])
    for index in range(99):
        analytic_distance = None
        if index < 3:
            delta = (-1e-6, 0, 1e-6)[index]
            radius = .25
            start = end = face + (.25 + delta) * normal
            analytic_distance = .25 + delta
        else:
            start, end = rng.uniform(2.3, 5.7, (2, 3)) @ affine[:3, :3].T
            radius = float(rng.uniform(0, .45))
        exact = reference.capsule_query(start, end, radius)
        conservative = backend.capsule_clearance(start, end, radius)
        rows.append({
            "index": index, "start_mm": start.tolist(), "end_mm": end.tolist(),
            "radius_mm": radius, "reference_distance_mm": exact.centerline_distance_mm,
            "reference_collision": exact.collides,
            "analytic_face_distance_mm": analytic_distance,
            "analytic_error_mm": None if analytic_distance is None
            else abs(exact.centerline_distance_mm - analytic_distance),
            "conservative_clearance_mm": conservative.clearance_mm,
            "out_of_fov": conservative.out_of_fov,
            "false_feasible": bool(exact.collides and not conservative.out_of_fov
                                  and conservative.clearance_mm > 1e-9),
            "conservative_only_collision": bool(not exact.collides and not conservative.out_of_fov
                                               and conservative.clearance_mm <= 0),
        })
    return {"affine": affine.tolist(), "cell_indices": [[4, 4, 4]], "rows": rows,
            "false_feasible_count": sum(r["false_feasible"] for r in rows),
            "reference_collision_count": sum(r["reference_collision"] for r in rows),
            "conservative_only_collision_count": sum(r["conservative_only_collision"] for r in rows),
            "runtime_seconds": perf_counter() - started}


def convergence_evidence():
    rows = []
    for arrangement in ("off_axis_11deg", "off_axis_19deg", "tilted_portal_cap"):
        base = approach(radius=0.)
        base["sampling"].update(max_angle_deg=45, target_directed=False, adaptive_levels=0,
                                portal_offset_rings=0)
        if arrangement == "tilted_portal_cap":
            tilt, half_angle = 12., 18.
            base["portal"]["normal"] = [math.sin(math.radians(tilt)), 0,
                                        math.cos(math.radians(tilt))]
            base["instrument"]["radius_mm"] = 1.
            base["portal"]["radius_mm"] = 1 / math.cos(math.radians(half_angle))
            base["target_tolerance_mm"] = 20.
            target = [0, 0, 10]
            expected = analytic_cap_solid_angle(half_angle)
            derivation = "r/cos(theta)<=R; 18-degree portal cap tilted 12 degrees, within 45-degree grid"
        else:
            tilt = 11. if arrangement == "off_axis_11deg" else 19.
            azimuth = 23. if tilt == 11. else 71.
            distance, tolerance = 10., 2.
            theta, phi = math.radians(tilt), math.radians(azimuth)
            target = [distance * math.sin(theta) * math.cos(phi),
                      distance * math.sin(theta) * math.sin(phi), distance * math.cos(theta)]
            base["target_tolerance_mm"] = tolerance
            expected = off_axis_target_solid_angle(distance, tolerance, tilt, 45.)
            derivation = "2*pi*(1-sqrt(1-(tolerance/distance)^2)); target cap wholly inside sampling cap"
        for polar, azimuth in ((5, 24), (9, 48), (17, 96), (33, 192)):
            data = json.loads(json.dumps(base))
            data["sampling"].update(polar_steps=polar, azimuth_steps=azimuth)
            case = CorridorCase.model_validate({
                "case_id": arrangement, "target": {"points_mm": [target]}, "approaches": [data]})
            started = perf_counter()
            row = {"arrangement": arrangement, "polar_steps": polar, "azimuth_steps": azimuth,
                   "expected_solid_angle_sr": expected, "derivation": derivation,
                   "parameters": case.model_dump(mode="json"),
                   "parameter_hash": parameter_hash(case.model_dump(mode="json")), "error": None}
            try:
                result = analyze_case(case).approaches[0]
                actual = result.feasible_solid_angle_sr
                row.update(status=result.status.value, solid_angle_sr=actual,
                           sample_count=len(result.trajectories),
                           absolute_error_sr=None if actual is None else abs(actual - expected),
                           relative_error=None if actual is None else abs(actual - expected) / expected)
            except Exception as exc:
                row.update(status="error", error={"type": type(exc).__name__, "message": str(exc)})
            row["runtime_seconds"] = perf_counter() - started
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "research/numerical-benchmark")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    source_hashes = hashes()
    rng = np.random.default_rng(SEED)
    evidence = {
        "schema_version": "1.0", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": SEED, "python": platform.python_version(),
        "numpy": np.__version__, "scipy": scipy.__version__,
        "source_hashes": source_hashes,
        "limitations": [
            "Synthetic mathematical phantoms only; no physical anatomy or clinical safety validation.",
            "Mesh reference is standalone and NOT integrated in the engine.",
            "Unsigned cell-solid distances only; signed mesh distance/penetration depth not solved.",
            "Exhaustive cell triangulation is unsuitable for full-size medical masks.",
            "Conservative backend may reject truly free capsules; no false feasible claim beyond tested samples.",
            "Base ray/cone uses actual engine with radius zero and finite insertion length; NOT visibility.",
            "Identical target-directed candidates across ablations; offsets/adaptation disabled.",
            "Pure quadrature convergence disables target witnesses, offsets and adaptation.",
            "Finite sampling and convergence do not certify completeness or infeasibility.",
        ],
    }
    evidence["ablations"] = ablation_evidence(args.output)
    evidence["sphere_reference"] = sphere_evidence(rng)
    evidence["mesh_reference"] = mesh_evidence(args.output, rng)
    evidence["quadrature_convergence"] = convergence_evidence()
    sampling_checks = {}
    for case_id in sorted({row["case_id"] for row in evidence["ablations"]}):
        models = [row for row in evidence["ablations"] if row["case_id"] == case_id]
        sampling_checks[case_id] = all(row["result"] is not None for row in models) and len({
            tuple(a["sampling_hash"] for a in row["result"]["approaches"])
            for row in models if row["result"] is not None
        }) == 1
    evidence["identical_ablation_sampling_by_case"] = sampling_checks
    evidence["mesh_reference"]["lower_bound_violation_count"] = sum(
        bool(not row["out_of_fov"] and row["conservative_clearance_mm"]
             > row["reference_distance_mm"] - row["radius_mm"] + 1e-9)
        for row in evidence["mesh_reference"]["rows"])
    evidence["source_hashes_after"] = hashes()
    evidence["sources_changed_during_run"] = source_hashes != evidence["source_hashes_after"]
    evidence["input_file_hashes"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.glob("*.npy")}
    evidence["parameter_hash"] = parameter_hash({
        "seed": SEED,
        "ablations": [r["parameter_hash"] for r in evidence["ablations"]],
        "quadrature": [r["parameter_hash"] for r in evidence["quadrature_convergence"]],
        "input_files": evidence["input_file_hashes"],
    })
    evidence["runtime_seconds"] = perf_counter() - started
    destination = args.output / "results.json"
    destination.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    summary = {
        "parameter_hash": evidence["parameter_hash"],
        "runtime_seconds": evidence["runtime_seconds"],
        "identical_ablation_sampling_by_case": sampling_checks,
        "ablations": [
            {"case_id": row["case_id"], "model": row["model"], "statuses": row["statuses"],
             "error": row["error"], "runtime_seconds": row["runtime_seconds"],
             "reached_counts": None if row["result"] is None else [
                 len(a["reached_point_indices"]) for a in row["result"]["approaches"]],
             "pairs_feasible": None if row["result"] is None else [
                 p["feasible"] for p in row["result"]["simultaneous_pairs"]]}
            for row in evidence["ablations"]],
        "sphere_max_absolute_error_mm": evidence["sphere_reference"]["max_absolute_error_mm"],
        "mesh_reference": {key: value for key, value in evidence["mesh_reference"].items()
                           if key != "rows"},
        "quadrature": [{key: value for key, value in row.items() if key != "parameters"}
                       for row in evidence["quadrature_convergence"]],
        "sources_changed_during_run": evidence["sources_changed_during_run"],
    }
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(json.dumps({
        "results": str(destination), "runtime_seconds": evidence["runtime_seconds"],
        "sphere_max_error_mm": evidence["sphere_reference"]["max_absolute_error_mm"],
        "mesh_false_feasible_count": evidence["mesh_reference"]["false_feasible_count"],
        "mesh_conservative_only_collision_count":
            evidence["mesh_reference"]["conservative_only_collision_count"],
        "sources_changed_during_run": evidence["sources_changed_during_run"],
        "execution_error_count": sum(r["error"] is not None
                                     for r in evidence["ablations"] + evidence["quadrature_convergence"]),
    }, indent=2))
    return 1 if (evidence["sources_changed_during_run"]
                 or evidence["mesh_reference"]["false_feasible_count"]
                 or evidence["mesh_reference"]["lower_bound_violation_count"]
                 or not all(sampling_checks.values())
                 or any(r["error"] for r in evidence["ablations"] + evidence["quadrature_convergence"])) else 0


if __name__ == "__main__":
    raise SystemExit(main())
