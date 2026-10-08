"""Independent aggregate and witness verification of the production cohort run."""
from pathlib import Path
import gzip
import json
import sys
from collections import Counter
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from corridorkit.export.json import file_sha256, atomic_json_write
from corridorkit.analysis.statistics import paired_summary


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "research/results/production-public-v2")
    directory = parser.parse_args().directory
    summary = json.loads((directory / "summary.json").read_text())
    manifest = json.loads((directory / "frozen-manifest.json").read_text())
    assert manifest["upstream_per_case_sha256"] == file_sha256(
        ROOT / "research/results/airspace-v1/per-case.jsonl.gz")
    assert file_sha256(directory / "per-case.jsonl.gz") == summary["per_case_sha256"]
    with gzip.open(directory / "per-case.jsonl.gz", "rt") as stream:
        records = [json.loads(line) for line in stream]
    assert len(records) == len(manifest["partition"]) == 130
    assert len({r["case_id"] for r in records}) == 130
    assert dict(Counter(r["status"] for r in records)) == summary["case_status_counts"]
    witness_count = 0
    for record in records:
        assert record["partition"] == manifest["partition"][record["case_id"]]
        if record["status"] != "evaluated":
            continue
        for name, result in record["models"].items():
            by_name = {}
            for approach in result["approaches"]:
                reached = set()
                for trajectory in approach["trajectories"]:
                    if not trajectory["feasible"]:
                        continue
                    direction = np.asarray(trajectory["direction"])
                    assert abs(np.linalg.norm(direction) - 1) < 1e-8
                    depths = trajectory["insertion_depths_mm"]
                    assert len(depths) == len(trajectory["reached_point_indices"])
                    assert all(0 <= d <= manifest["config"]["length_mm"] + 1e-8 for d in depths)
                    assert trajectory["minimum_clearance_mm"] is None or trajectory["minimum_clearance_mm"] > 0
                    reached.update(trajectory["reached_point_indices"])
                    witness_count += 1
                assert reached == set(approach["reached_point_indices"])
                assert all(0 <= i < approach["target_count"] for i in reached)
                by_name[approach["name"]] = reached
            for combo in result["combinations"]:
                a, b = by_name[combo["first"]], by_name[combo["second"]]
                assert set(combo["union_indices"]) == a | b
                assert set(combo["intersection_indices"]) == a & b
                # Implementation defines incremental-first as first minus second.
                assert set(combo["incremental_first_indices"]) == a - b
                assert set(combo["incremental_second_indices"]) == b - a
            for pair in result["simultaneous_pairs"]:
                if pair["feasible"]:
                    assert pair["actual_angle_deg"] >= pair["minimum_angle_deg"] - 1e-8
                    assert pair["shaft_clearance_mm"] > 0
    for partition, expected in summary["paired_finite_minus_ray_union"].items():
        groups = {"ray": {}, "finite": {}}
        for record in records:
            if record["status"] != "evaluated" or record["partition"] != partition:
                continue
            for model, result in record["models"].items():
                if any(a["status"] in ("abstained", "incomplete", "unsupported") for a in result["approaches"]):
                    continue
                reached = set().union(*(set(a["reached_point_indices"]) for a in result["approaches"]))
                groups[model][record["case_id"]] = len(reached) / result["approaches"][0]["target_count"]
        assert paired_summary(groups["ray"], groups["finite"]) == expected
    result = {"status": "passed", "records": len(records), "feasible_witness_records": witness_count,
              "checks": ["hash", "partition", "uniqueness", "aggregate statuses", "witness units",
                         "depth bounds", "union/intersection/incremental identities", "pair constraints",
                         "recomputed paired bootstrap"],
              "not_checked": "No independent operative anatomy or surgical reference exists."}
    atomic_json_write(directory / "verification.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
