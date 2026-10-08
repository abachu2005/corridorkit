"""Run the actual corridor engine on every eligible public computational case.

These internal airspace openings/targets are controlled test geometry, NOT EEA
or transmaxillary surgical openings. Approach kind enums exercise comparison
code only; their surgical labels have no anatomical meaning in this benchmark.
"""
from pathlib import Path
import gzip
import hashlib
import json
import tempfile
import time
import zipfile
import sys
from collections import Counter

import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from corridorkit.data.audit import _load
from corridorkit.domain.models import CorridorCase
from corridorkit.geometry.engine import analyze_case
from corridorkit.export.json import atomic_json_write, file_sha256, checksum
from corridorkit.analysis.statistics import paired_summary

CONFIG = {
    "name": "production-public-airspace-v1",
    "models": {"ray": 0.0, "finite": 1.0},
    "length_mm": 60, "portal_radius_mm": 5,
    "polar_steps": 3, "azimuth_steps": 8, "max_angle_deg": 89,
    "target_tolerance_mm": 0.1, "adaptive_levels": 0,
    "simultaneous_minimum_angle_deg": 20,
    "interpretation": "Internal airspace computational openings; NOT operative corridors.",
}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "research/results/production-public-v2")
    args = parser.parse_args()
    output = args.output
    if output.exists():
        raise ValueError("Frozen output exists; do not overwrite")
    source = ROOT / "research/results/airspace-v1"
    manifest = json.loads((source / "frozen-manifest.json").read_text())
    upstream = json.loads((source / "summary.json").read_text())
    upstream_records_hash = file_sha256(source / "per-case.jsonl.gz")
    if upstream_records_hash != upstream["per_case_records_sha256"]:
        raise ValueError("Upstream target/anchor records changed")
    archive = ROOT / "research/data-cache/NasalSeg-v2.zip"
    if file_sha256(archive) != manifest["source"]["sha256"]:
        raise ValueError("Source archive mismatch")
    output.mkdir(parents=True)
    frozen = {
        "config": CONFIG, "input_manifest_sha256": file_sha256(source / "frozen-manifest.json"),
        "upstream_per_case_sha256": upstream_records_hash,
        "partition": manifest["subject_split"], "frozen_before_analysis": True,
        "code_sha256": {str(p.relative_to(ROOT)): file_sha256(p)
                        for p in sorted((ROOT / "src").rglob("*.py"))},
        "runner_sha256": file_sha256(Path(__file__)),
        "scope": "Production engine integration benchmark, not surgical validation",
    }
    atomic_json_write(output / "frozen-manifest.json", frozen)
    records = []
    started = time.perf_counter()
    with gzip.open(source / "per-case.jsonl.gz", "rt") as stream:
        inputs = [json.loads(line) for line in stream]
    with zipfile.ZipFile(archive) as z:
        for number, record in enumerate(inputs, 1):
            identifier = record["case_id"]
            result_record = {"case_id": identifier, "partition": manifest["subject_split"][identifier]}
            if record["status"] != "evaluated":
                result_record.update(status="excluded", reasons=record.get("exclusion_reasons", []))
                records.append(result_record)
                continue
            try:
                with tempfile.TemporaryDirectory(prefix="corridor-engine-") as temporary:
                    temporary = Path(temporary)
                    label_path = temporary / "label.nrrd"
                    label_path.write_bytes(z.read(manifest["label_members"][identifier]))
                    labels, affine = _load(label_path)
                    # Use the same explicitly computational boundary as the reference
                    # benchmark. It is NOT complete bone or neurovascular anatomy.
                    air = labels > 0
                    boundary = ndimage.binary_dilation(air) & ~air
                    np.save(temporary / "boundary.npy", boundary)
                    detail = record.get("analysis", record.get("benchmark", record))
                    if "targets" not in detail:
                        detail = next(v for v in record.values() if isinstance(v, dict) and "targets" in v)
                    points = np.asarray([p["point_ras_mm"] for p in detail["targets"]])
                    approaches = []
                    for index, anchor in enumerate(detail["anchors"]):
                        center = np.asarray(anchor["point_ras_mm"])
                        direction = points.mean(axis=0) - center
                        if np.linalg.norm(direction) < 1e-9:
                            direction = np.array([0, 0, 1])
                        direction /= np.linalg.norm(direction)
                        approaches.append({
                            "name": f"Computational opening {index + 1}",
                            "kind": "eea" if index == 0 else "transmaxillary",
                            "portal": {"center_mm": center.tolist(), "normal": direction.tolist(),
                                       "radius_mm": CONFIG["portal_radius_mm"]},
                            "nominal_direction": direction.tolist(),
                            "instrument": {"length_mm": CONFIG["length_mm"], "radius_mm": 0},
                            "target_tolerance_mm": CONFIG["target_tolerance_mm"],
                            "sampling": {k: CONFIG[k] for k in ("polar_steps", "azimuth_steps",
                                                                "max_angle_deg", "adaptive_levels")},
                        })
                    data = {
                        "case_id": identifier, "coordinate_frame": "RAS",
                        "target": {"points_mm": points.tolist(), "source": "public_airspace_computational_points"},
                        "protected_structures": [{
                            "name": "computational_airspace_boundary_not_critical_anatomy",
                            "geometry": {"kind": "voxel", "uri": "boundary.npy", "affine": affine.tolist()},
                        }], "approaches": approaches,
                    }
                    outputs = {}
                    configurations = {}
                    for model, radius in CONFIG["models"].items():
                        for a in data["approaches"]:
                            a["instrument"]["radius_mm"] = radius
                        case = CorridorCase.model_validate(data)
                        configurations[model] = case.model_dump(mode="json")
                        result = analyze_case(case, base_directory=temporary,
                                              simultaneous_minimum_angle_deg=20)
                        outputs[model] = result.model_dump(mode="json")
                    result_record.update(status="evaluated", models=outputs,
                                         configurations=configurations,
                                         geometry_config_sha256=checksum(data))
            except Exception as exc:
                result_record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            records.append(result_record)
            print(f"{number}/130 {identifier}: {result_record['status']}", flush=True)
    with gzip.open(output / "per-case.jsonl.gz", "wt") as stream:
        for record in records:
            stream.write(json.dumps(record, allow_nan=False) + "\n")
    statuses = Counter(r["status"] for r in records)
    approach_statuses = Counter()
    pairs = Counter()
    effects = {}
    for partition in ("development", "validation", "evaluation"):
        ray, finite = {}, {}
        for record in records:
            if record["status"] != "evaluated":
                continue
            for model, result in record["models"].items():
                approach_statuses.update(f"{model}/{a['status']}" for a in result["approaches"])
                pairs.update(f"{model}/{p['status']}/{p['feasible']}" for p in result["simultaneous_pairs"])
            if record["partition"] != partition:
                continue
            for model, destination in (("ray", ray), ("finite", finite)):
                result = record["models"][model]
                if any(a["status"] in ("abstained", "incomplete", "unsupported") for a in result["approaches"]):
                    continue
                union = set().union(*(set(a["reached_point_indices"]) for a in result["approaches"]))
                destination[record["case_id"]] = len(union) / result["approaches"][0]["target_count"]
        effects[partition] = paired_summary(ray, finite)
    # Each partition loop inspects all cases, so normalize bookkeeping counts.
    approach_statuses = {k: v // 3 for k, v in approach_statuses.items()}
    pairs = {k: v // 3 for k, v in pairs.items()}
    summary = {
        "scope": CONFIG["interpretation"], "production_engine_exercised": True,
        "case_status_counts": dict(statuses), "approach_status_counts": approach_statuses,
        "simultaneous_status_counts": pairs, "paired_finite_minus_ray_union": effects,
        "runtime_seconds": time.perf_counter() - started,
        "reviewed_surgical_anatomy": False, "operative_validity": "not_established",
        "per_case_sha256": file_sha256(output / "per-case.jsonl.gz"),
    }
    atomic_json_write(output / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    if statuses.get("failed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
