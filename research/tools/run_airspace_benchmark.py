"""Frozen, streaming public benchmark. No images are copied into result folders."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from download_nasalseg import EXPECTED_MD5, URL, checksums
from skullbase_corridor.analysis.airspace import CONFIG, evaluate_airspace
from skullbase_corridor.analysis.statistics import paired_summary, subject_split
from skullbase_corridor.data.audit import _load, _sha256, audit_pair, index_cases


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "research/data-cache/NasalSeg-v2.zip")
    parser.add_argument("--output", type=Path, default=ROOT / "research/results/airspace-v1")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Output already exists; select a new directory to preserve frozen results")
    source = checksums(args.archive)
    if source["md5"] != EXPECTED_MD5:
        raise ValueError("Archive checksum differs from prespecified NasalSeg v2")
    source.update(url=URL, expected_md5=EXPECTED_MD5, record="13893419", version="v2", license="CC-BY-4.0")
    started = time.perf_counter()
    with zipfile.ZipFile(args.archive) as archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        images, labels, ignored = [], [], []
        for member in members:
            path = Path(member.filename)
            if path.name.endswith("_img.nrrd") and "__MACOSX" not in path.parts:
                images.append(path)
            elif path.name.endswith("_seg.nrrd") and "__MACOSX" not in path.parts:
                labels.append(path)
            else:
                ignored.append(member.filename)
        image_ids, label_ids = index_cases(images), index_cases(labels)
        identifiers = sorted(image_ids.keys() | label_ids.keys())
        if not identifiers:
            raise ValueError("No recognized _img.nrrd or _seg.nrrd members")
        split = subject_split(
            identifiers, development_fraction=CONFIG["development_fraction"],
            validation_fraction=CONFIG["validation_fraction"], salt=CONFIG["split_salt"],
        )
        # Freeze exact identifiers and engineering grid BEFORE loading any image.
        code_paths = [
            Path(__file__), ROOT / "research/tools/download_nasalseg.py",
            ROOT / "src/skullbase_corridor/analysis/airspace.py",
            ROOT / "src/skullbase_corridor/analysis/statistics.py",
            ROOT / "src/skullbase_corridor/data/audit.py",
            ROOT / "research/AIRSPACE_BENCHMARK_PROTOCOL.md",
        ]
        manifest = {
            "schema_version": "1.0", "frozen_before_case_loading": True,
            "started_utc": datetime.now(timezone.utc).isoformat(),
            "source": source, "config": CONFIG, "subject_split": split,
            "image_members": {key: str(value) for key, value in image_ids.items()},
            "label_members": {key: str(value) for key, value in label_ids.items()},
            "ignored_members": ignored,
            "source_code_sha256": {str(path.relative_to(ROOT)): _sha256(path) for path in code_paths},
            "environment": {
                "python": platform.python_version(), "platform": platform.platform(),
                "packages": {name: importlib.metadata.version(name) for name in
                             ["numpy", "scipy", "SimpleITK", "nibabel"]},
            },
            "command": ["python", str(Path(__file__).relative_to(ROOT)), "--archive",
                        str(args.archive), "--output", str(args.output)],
            "expert_anatomical_review": "incomplete",
        }
        args.output.mkdir(parents=True)
        write_json(args.output / "frozen-manifest.json", manifest)
        manifest_hash = hashlib.sha256(canonical(manifest)).hexdigest()
        print(f"FROZEN manifest_sha256={manifest_hash} identifiers={len(identifiers)}", flush=True)
        audits, cases, statuses, exclusions = [], [], Counter(), Counter()
        effects = {partition: ({}, {}) for partition in ["development", "validation", "evaluation"]}
        comparison = {"classification_mismatches": 0, "false_feasible": 0,
                      "scenario_segment_count": 0, "grid_classification_count": 0,
                      "max_capped_clearance_absolute_error_mm": 0.0}
        counts = Counter()
        # Gzip avoids large repository results; JSONL retains complete per-case data.
        with (args.output / "per-case.jsonl.gz").open("xb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as records:
                for number, identifier in enumerate(identifiers, 1):
                    case_start = time.perf_counter()
                    record = {"case_id": identifier, "partition": split[identifier]}
                    if identifier not in image_ids or identifier not in label_ids:
                        record.update(status="excluded", exclusion_reasons=[
                            "missing_image" if identifier not in image_ids else "missing_label"
                        ])
                    else:
                        try:
                            pair_paths = [image_ids[identifier], label_ids[identifier]]
                            required = sum(archive.getinfo(str(path)).file_size for path in pair_paths)
                            if shutil.disk_usage(args.archive.parent).free < required + 64 * 1024**2:
                                raise OSError("Insufficient disk for one case plus 64 MiB reserve")
                            # Only this context's owned temporary directory is deleted.
                            with tempfile.TemporaryDirectory(prefix="airspace-case-", dir=args.archive.parent) as directory:
                                local = []
                                for path in pair_paths:
                                    destination = Path(directory) / path.name
                                    with archive.open(str(path)) as incoming, destination.open("xb") as outgoing:
                                        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                                    local.append(destination)
                                audit = audit_pair(*local)
                                audit["image"], audit["label"] = map(str, pair_paths)
                                audits.append(audit)
                                record["audit"] = audit
                                if not audit["engineering_eligible"]:
                                    record.update(status="excluded", exclusion_reasons=audit["exclusion_reasons"])
                                else:
                                    label, affine = _load(local[1])
                                    record.update(evaluate_airspace(label, affine))
                        except Exception as exc:
                            record.update(status="error", exclusion_reasons=["case_processing_error"],
                                          error=f"{type(exc).__name__}: {exc}")
                    statuses[record["status"]] += 1
                    exclusions.update(record.get("exclusion_reasons", []))
                    if record["status"] == "evaluated":
                        for key, value in record["geometry_comparison"].items():
                            if key.startswith("max_"):
                                comparison[key] = max(comparison[key], value)
                            else:
                                comparison[key] += value
                        for scenario, grid in record["counts"].items():
                            for key, classified in grid.items():
                                for status, count in classified.items():
                                    counts[f"{scenario}/{key}/{status}"] += count
                        a, b = effects[split[identifier]]
                        a[identifier] = record["conservative_coverage"]["baseline"]["r0_l30"]
                        b[identifier] = record["conservative_coverage"]["baseline"]["r1_l30"]
                    record["runtime_seconds"] = time.perf_counter() - case_start
                    records.write(canonical(record) + b"\n")
                    cases.append({key: record[key] for key in
                                  ["case_id", "partition", "status", "runtime_seconds"]} |
                                 {"exclusion_reasons": record.get("exclusion_reasons", [])})
                    print(f"{number:03d}/{len(identifiers)} {identifier} {record['status']} "
                          f"{record.get('exclusion_reasons', [])} {record['runtime_seconds']:.2f}s", flush=True)
        partition_summaries = {}
        for partition, (a, b) in effects.items():
            partition_summaries[partition] = (
                paired_summary(a, b, bootstrap_repeats=CONFIG["bootstrap_repeats"], seed=CONFIG["seed"])
                if len(a) >= 2 else {"n_subjects": len(a), "status": "insufficient_paired_subjects"}
            )
        summary = {
            "schema_version": "1.0",
            "benchmark": "scan_based_controlled_airspace_computational_benchmark",
            "source": source, "frozen_manifest_sha256": manifest_hash,
            "n_source_identifiers": len(identifiers), "n_image_members": len(images),
            "n_label_members": len(labels), "case_status_counts": dict(statuses),
            "exclusion_reason_counts": dict(exclusions), "cases": cases,
            "geometry_comparison": comparison, "grid_status_counts": dict(counts),
            "prespecified_contrast": "baseline 1mm minus 0mm radius, 30mm length; equal subject weights",
            "paired_contrast_by_partition": partition_summaries,
            "expert_anatomical_review": "incomplete", "surgical_validation": "not_performed",
            "engine_integration": "independent airspace backend; production corridor engine not evaluated",
            "full_craniofacial_coverage": False,
            "runtime_seconds": time.perf_counter() - started,
            "per_case_records_sha256": _sha256(args.output / "per-case.jsonl.gz"),
            "interpretation": (
                "Agreement only between two implementations of a boundary-sphere computational model. "
                "No surgical routes, vessel anatomy, petroclival targets, or surgical validation. "
                "P identifiers proxy subjects; independence across source IDs is unverified."
            ),
        }
        write_json(args.output / "integrity-audit.json", {"source": source, "records": audits})
        write_json(args.output / "summary.json", summary)
        print(json.dumps({key: summary[key] for key in [
            "case_status_counts", "exclusion_reason_counts", "geometry_comparison",
            "runtime_seconds", "per_case_records_sha256",
        ]}, indent=2))
        if statuses["error"]:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
