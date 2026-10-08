#!/usr/bin/env python3
"""Frozen, reproducible analytic checks through the actual production engine.

Run: PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python research/run_geometric_angular.py
Use --output research/geometric-angular-replay for a separate non-overwriting run.
"""

from __future__ import annotations

import argparse
import copy
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

from corridorkit.domain.models import CorridorCase
from corridorkit.geometry.engine import analyze_case


def digest(data):
    return hashlib.sha256(data).hexdigest()


def direction(tilt, azimuth):
    t, p = np.radians([tilt, azimuth])
    return np.array([math.sin(t) * math.cos(p), math.sin(t) * math.sin(p), math.cos(t)])


def cap_area(radius):
    return 4 * math.pi * math.sin(math.radians(radius) / 2) ** 2


def lens(radius, separation):
    r, d = np.radians([radius, separation])
    alpha = math.acos(math.cos(r) / math.sin(r) * math.tan(d / 2))
    gamma = math.acos((math.cos(d) - math.cos(r) ** 2) / math.sin(r) ** 2)
    return 2 * math.pi - 4 * alpha * math.cos(r) - 2 * gamma


def case(name, tilt=19., azimuth=71., radius=math.degrees(math.asin(.2))):
    return {
        "case_id": name,
        "target": {"points_mm": [(10 * direction(tilt, azimuth)).tolist()]},
        "approaches": [{
            "name": "A", "kind": "eea",
            "portal": {"center_mm": [0, 0, 0], "normal": [0, 0, 1], "radius_mm": 2},
            "nominal_direction": [0, 0, 1],
            "instrument": {"length_mm": 20, "radius_mm": 0},
            "sampling": {"max_angle_deg": 45, "polar_steps": 5, "azimuth_steps": 24,
                         "target_directed": False, "adaptive_levels": 0},
            "target_tolerance_mm": 10 * math.sin(math.radians(radius)),
        }],
    }


def problems(protocol, original):
    for spec in original["cases"]:
        radius = (spec["cap_half_angle_deg"] if spec["predicate"] == "angular_cap"
                  else math.degrees(math.asin(.2)))
        data = case(spec["name"], spec["tilt_deg"], spec["azimuth_deg"], radius)
        expected = cap_area(radius)
        if spec["predicate"] == "target_radial_tolerance":
            distance, tolerance = spec["target_distance_mm"], spec["target_tolerance_mm"]
            data["target"]["points_mm"] = [
                (distance * direction(spec["tilt_deg"], spec["azimuth_deg"])).tolist()]
            data["approaches"][0]["target_tolerance_mm"] = tolerance
            data["approaches"][0]["instrument"]["length_mm"] = spec["instrument_length_mm"]
            expected = 2 * math.pi * (1 - math.sqrt(1 - (tolerance / distance) ** 2))
        elif spec["predicate"] == "portal_footprint":
            base = data["approaches"][0]
            base["portal"]["normal"] = direction(spec["tilt_deg"], spec["azimuth_deg"]).tolist()
            base["portal"]["radius_mm"] = (
                spec["shaft_radius_mm"] / math.cos(math.radians(spec["portal_half_angle_deg"])))
            base["instrument"]["radius_mm"] = spec["shaft_radius_mm"]
            base["target_tolerance_mm"] = 20.
            data["target"]["points_mm"] = [[0, 0, 10]]
            expected = cap_area(spec["portal_half_angle_deg"])
        for polar, azimuth in protocol["original_grid_sizes"]:
            sampled = copy.deepcopy(data)
            sampled["approaches"][0]["sampling"].update(polar_steps=polar, azimuth_steps=azimuth)
            yield sampled, expected, "original_" + spec["role"], "contained cap closed form"

    rng = np.random.default_rng(protocol["seed"])
    extra = protocol["additional_cases"]
    low, high = extra["cap_radius_range_deg_log_uniform"]
    for index in range(extra["rotated_caps"]):
        radius = math.exp(rng.uniform(math.log(low), math.log(high)))
        tilt, azimuth = rng.uniform(0, 45 - radius - .05), rng.uniform(0, 360)
        data = case(f"random_cap_{index:02}", tilt, azimuth, radius)
        yield data, cap_area(radius), "random_cap", "contained cap closed form"

    for index in range(extra["random_rotation_covariance_cases"]):
        data = case(f"frame_rotation_{index:02}", 11, 23)
        rotation, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        data["target"]["points_mm"] = (np.array(data["target"]["points_mm"]) @ rotation.T).tolist()
        base = data["approaches"][0]
        base["nominal_direction"] = rotation[:, 2].tolist()
        base["portal"]["normal"] = rotation[:, 2].tolist()
        yield data, 2 * math.pi * (1 - math.sqrt(.96)), "rotated_frame", "rotation invariant cap"

    for index in range(extra["finite_length_annuli"]):
        data = case(f"finite_annulus_{index}", 0, 0)
        lower = math.sqrt(100 - 4)
        length = lower + .001 * 2 ** index
        data["approaches"][0]["instrument"]["length_mm"] = length
        yield data, 2 * math.pi * (length - lower) / 10, "finite_length", "2*pi*axial_band_width/distance"

    for separation in extra["equal_cap_clipped_lens_separations_deg"]:
        data = case(f"clipped_lens_{separation}", separation, 30, 20)
        data["approaches"][0]["sampling"]["max_angle_deg"] = 20
        yield data, lens(20, separation), "domain_clipping", "independent Gauss-Bonnet lens formula"

    for separation in extra["overlapping_target_union_separations_deg"]:
        data = case(f"target_union_{separation}", 0, 0, 15)
        data["target"]["points_mm"].append((10 * direction(separation, 0)).tolist())
        yield data, 2 * cap_area(15) - lens(15, separation), "target_union", "inclusion-exclusion of analytic lens"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "research/geometric-angular")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "research"):
        raise SystemExit("Outputs must remain inside this repository's research directory.")
    if (output / "results.json").exists():
        raise SystemExit("Frozen results exist; choose a new --output directory.")
    started = perf_counter()
    protocol_path = ROOT / "research/geometric-angular/protocol.json"
    protocol_bytes = protocol_path.read_bytes()
    protocol = json.loads(protocol_bytes)
    original = json.loads((ROOT / "research/refined-angular/protocol.json").read_bytes())
    source_paths = [
        Path(__file__).resolve(),
        ROOT / "src/corridorkit/analysis/geometric_angular.py",
        ROOT / "src/corridorkit/geometry/engine.py",
        ROOT / "src/corridorkit/geometry/primitives.py",
        ROOT / "src/corridorkit/domain/models.py",
        ROOT / "tests/test_geometric_angular.py",
    ]
    sources = {str(p.relative_to(ROOT)): p.read_bytes() for p in source_paths}
    preserved = [p for folder in ("numerical-benchmark", "refined-angular")
                 for p in sorted((ROOT / "research" / folder).rglob("*")) if p.is_file()]
    previous = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in preserved}
    rows = []
    for data, expected, category, reference in problems(protocol, original):
        row = dict(case=data, category=category, reference=reference,
                   expected_solid_angle_sr=expected, exception=None,
                   parameter_sha256=digest(json.dumps(data, sort_keys=True).encode()))
        began = perf_counter()
        try:
            result = analyze_case(CorridorCase.model_validate(data)).approaches[0]
            area = result.feasible_solid_angle_sr
            error = None if area is None else abs(area - expected) / expected
            row.update(
                solid_angle_sr=area, relative_error=error,
                target_met=error is not None and error <= protocol["engineering_acceptance_relative_error"],
                sampled_trajectory_count=len(result.trajectories),
                feasible_trajectory_count=result.feasible_trajectory_count,
                status=result.status.value,
                integration_notes=[n for n in result.notes if "Solid angle" in n],
            )
        except Exception as exc:
            row.update(exception={"type": type(exc).__name__, "message": str(exc)}, target_met=False)
        row["runtime_seconds"] = perf_counter() - began
        rows.append(row)
    after = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in preserved}
    source_hashes = {name: digest(data) for name, data in sources.items()}
    evidence = dict(
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
        protocol=protocol, protocol_sha256=digest(protocol_bytes),
        source_hashes=source_hashes,
        source_hashes_after={str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in source_paths},
        previous_evidence_hashes_before=previous, previous_evidence_hashes_after=after,
        previous_evidence_unchanged=previous == after,
        protocol_unchanged=protocol_bytes == protocol_path.read_bytes(),
        rows=rows, all_analytic_targets_met=all(row["target_met"] for row in rows),
        general_protected_geometry_gate_met=False,
        total_runtime_seconds=perf_counter() - started,
    )
    evidence["sources_unchanged"] = source_hashes == evidence["source_hashes_after"]
    output.mkdir(parents=True, exist_ok=True)
    for name, data in sources.items():
        path = output / "frozen_sources" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (output / "protocol.json").write_bytes(protocol_bytes)
    result_path = output / "results.json"
    result_path.write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    checksum_paths = [output / "protocol.json", result_path,
                      *(output / "frozen_sources" / name for name in sources)]
    (output / "SHA256SUMS").write_text("".join(
        f"{digest(p.read_bytes())}  {p.relative_to(ROOT)}\n" for p in checksum_paths))
    summary = dict(
        results=str(result_path), case_count=len(rows),
        all_analytic_targets_met=evidence["all_analytic_targets_met"],
        general_protected_geometry_gate_met=False,
        previous_evidence_unchanged=evidence["previous_evidence_unchanged"],
        sources_unchanged=evidence["sources_unchanged"],
        max_relative_error=max((r.get("relative_error") or 0) for r in rows),
        failures=[r for r in rows if not r["target_met"]],
        categories={category: {
            "count": sum(r["category"] == category for r in rows),
            "max_relative_error": max((r.get("relative_error") or 0) for r in rows
                                      if r["category"] == category),
        } for category in sorted({r["category"] for r in rows})},
        total_runtime_seconds=evidence["total_runtime_seconds"],
    )
    print(json.dumps(summary, indent=2))
    return 0 if (evidence["all_analytic_targets_met"] and evidence["previous_evidence_unchanged"]
                 and evidence["sources_unchanged"] and evidence["protocol_unchanged"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
