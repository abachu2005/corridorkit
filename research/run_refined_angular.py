#!/usr/bin/env python3
"""Run the predeclared standalone angular experiment; do not tune from results."""

from __future__ import annotations

import hashlib
import json
import math
import platform
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from corridorkit.analysis.angular_integration import integrate_solid_angle

OUTPUT = ROOT / "research/refined-angular"
PROTOCOL = OUTPUT / "protocol.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def analytic_problem(config):
    theta, phi = math.radians(config["tilt_deg"]), math.radians(config["azimuth_deg"])
    axis = np.array([math.sin(theta) * math.cos(phi),
                     math.sin(theta) * math.sin(phi), math.cos(theta)])
    if config["predicate"] == "target_radial_tolerance":
        distance, tolerance = config["target_distance_mm"], config["target_tolerance_mm"]
        target = distance * axis
        length = config["instrument_length_mm"]
        def predicate(direction):
            axial = float(target @ direction)
            return bool(0 <= axial <= length and np.linalg.norm(target - axial * direction) <= tolerance)
        half_angle = math.asin(tolerance / distance)
        expected = 2 * math.pi * (1 - math.sqrt(1 - (tolerance / distance) ** 2))
    elif config["predicate"] == "portal_footprint":
        half_angle = math.radians(config["portal_half_angle_deg"])
        radius = config["shaft_radius_mm"]
        portal_radius = radius / math.cos(half_angle)
        def predicate(direction):
            cosine = float(direction @ axis)
            return bool(cosine > 0 and radius / cosine <= portal_radius)
        expected = 2 * math.pi * (1 - math.cos(half_angle))
    elif config["predicate"] == "angular_cap":
        half_angle = math.radians(config["cap_half_angle_deg"])
        cosine = math.cos(half_angle)
        def predicate(direction):
            return bool(direction @ axis >= cosine)
        expected = 2 * math.pi * (1 - cosine)
    else:
        raise ValueError("unknown analytic problem")
    return predicate, expected, math.degrees(half_angle)


def main():
    # Preserve completed runs. A future experiment must use a new directory/script.
    if (OUTPUT / "results.json").exists():
        raise SystemExit("Frozen results already exist; refusing to overwrite this experiment.")
    started = perf_counter()
    protocol_bytes = PROTOCOL.read_bytes()
    protocol = json.loads(protocol_bytes)
    paths = [
        Path(__file__).resolve(),
        ROOT / "src/corridorkit/analysis/angular_integration.py",
        ROOT / "tests/test_angular_integration.py",
    ]
    source_bytes = {str(p.relative_to(ROOT)): p.read_bytes() for p in paths}
    baseline_paths = [
        ROOT / "research/numerical-benchmark" / name
        for name in ("results.json", "summary.json", "README.md", "SHA256SUMS",
                     "empty_fov_mask.npy", "single_cell_mask.npy")
    ]
    baseline_hashes = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in baseline_paths}
    rows = []
    prior = {}
    for config in protocol["cases"]:
        predicate, expected, half_angle = analytic_problem(config)
        if half_angle + config["tilt_deg"] > protocol["integration"]["half_angle_deg"]:
            raise ValueError("analytic expected area requires cap entirely within integration domain")
        for budget in protocol["sample_budgets"]:
            parameters = dict(protocol["integration"], max_samples=budget)
            row = {
                "case": config, "parameters": parameters,
                "expected_solid_angle_sr": expected, "exception": None,
                "parameter_hash": digest(json.dumps(
                    {"case": config, "integration": parameters}, sort_keys=True).encode()),
            }
            try:
                result = integrate_solid_angle(predicate, **parameters)
                actual_error = abs(result.solid_angle_sr - expected)
                row.update(
                    result=asdict(result), actual_absolute_error_sr=actual_error,
                    actual_relative_error=actual_error / expected,
                    engineering_target_met=actual_error / expected
                    <= protocol["engineering_acceptance_relative_error"],
                    change_from_previous_budget_sr=None if config["name"] not in prior
                    else abs(result.solid_angle_sr - prior[config["name"]]),
                )
                prior[config["name"]] = result.solid_angle_sr
            except Exception as exc:
                row.update(exception={"type": type(exc).__name__, "message": str(exc)},
                           engineering_target_met=False)
            rows.append(row)
    evidence = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": protocol, "protocol_sha256": digest(protocol_bytes),
        "source_hashes": {name: digest(data) for name, data in source_bytes.items()},
        "source_hashes_after": {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in paths},
        "original_evidence_hashes_before": baseline_hashes,
        "original_evidence_hashes_after": {
            str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in baseline_paths},
        "python": platform.python_version(), "numpy": np.__version__,
        "rows": rows, "total_runtime_seconds": perf_counter() - started,
    }
    evidence["original_evidence_unchanged"] = (
        evidence["original_evidence_hashes_before"] == evidence["original_evidence_hashes_after"])
    evidence["sources_unchanged"] = evidence["source_hashes"] == evidence["source_hashes_after"]
    evidence["protocol_unchanged"] = protocol_bytes == PROTOCOL.read_bytes()
    evidence["primary_engineering_target_met"] = all(
        row["engineering_target_met"] for row in rows
        if row["case"]["role"] == "primary"
        and row["parameters"]["max_samples"] == max(protocol["sample_budgets"]))
    for name, data in source_bytes.items():
        destination = OUTPUT / "frozen_sources" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    destination = OUTPUT / "results.json"
    destination.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    checksum_paths = [PROTOCOL, destination, *(OUTPUT / "frozen_sources" / name for name in source_bytes)]
    (OUTPUT / "SHA256SUMS").write_text("".join(
        f"{digest(path.read_bytes())}  {path.relative_to(ROOT)}\n" for path in checksum_paths))
    print(json.dumps({
        "results": str(destination), "primary_engineering_target_met":
        evidence["primary_engineering_target_met"], "original_evidence_unchanged":
        evidence["original_evidence_unchanged"], "runtime_seconds": evidence["total_runtime_seconds"],
        "rows": [{"case": row["case"]["name"], "budget": row["parameters"]["max_samples"],
                  "relative_error": row.get("actual_relative_error"),
                  "samples": row.get("result", {}).get("sample_count"),
                  "runtime_seconds": row.get("result", {}).get("elapsed_seconds"),
                  "termination": row.get("result", {}).get("termination_reason"),
                  "exception": row["exception"]} for row in rows],
    }, indent=2))
    return 0 if (evidence["sources_unchanged"] and evidence["original_evidence_unchanged"]
                 and evidence["protocol_unchanged"] and not any(r["exception"] for r in rows)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
