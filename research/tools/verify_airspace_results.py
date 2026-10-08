"""Validate report accounting and reproducibility without image extraction."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def verify(directory):
    manifest = json.loads((directory / "frozen-manifest.json").read_text())
    summary = json.loads((directory / "summary.json").read_text())
    with gzip.open(directory / "per-case.jsonl.gz", "rt") as stream:
        records = [json.loads(line) for line in stream]
    assert hashlib.sha256(canonical(manifest)).hexdigest() == summary["frozen_manifest_sha256"]
    assert hashlib.sha256((directory / "per-case.jsonl.gz").read_bytes()).hexdigest() == summary["per_case_records_sha256"]
    assert len(records) == len(manifest["subject_split"])
    assert len({record["case_id"] for record in records}) == len(records)
    assert {record["case_id"] for record in records} == set(manifest["subject_split"])
    assert dict(Counter(record["status"] for record in records)) == summary["case_status_counts"]
    for record in records:
        assert record["partition"] == manifest["subject_split"][record["case_id"]]
        if record["status"] == "evaluated":
            assert record["expert_anatomical_review"] == "incomplete"
            assert record["audit"]["engineering_eligible"]
            for scenario, grid in record["counts"].items():
                for key, counts in grid.items():
                    assert sum(counts.values()) == len(record["trajectories"])
                    assert record["conservative_coverage"][scenario][key] == counts["reached"] / sum(counts.values())
            for trajectory in record["trajectories"]:
                assert len(trajectory["scenarios"]) == len(manifest["config"]["scenarios"])
        else:
            assert record["exclusion_reasons"]
        record.pop("runtime_seconds")
    result = {
        "directory": str(directory), "case_records": len(records),
        "status_counts": summary["case_status_counts"],
        "deterministic_records_sha256_excluding_runtime": hashlib.sha256(canonical(records)).hexdigest(),
        "source_sha256": manifest["source"]["sha256"],
        "manifest_sha256": summary["frozen_manifest_sha256"],
        "accounting_verified": True,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    if args.compare:
        comparison = verify(args.compare)
        assert (result["deterministic_records_sha256_excluding_runtime"] ==
                comparison["deterministic_records_sha256_excluding_runtime"])
        result["reproduction"] = comparison
        result["exact_nontiming_record_reproduction"] = True
    if args.write:
        with args.write.open("x") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
