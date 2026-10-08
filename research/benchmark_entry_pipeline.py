#!/usr/bin/env python3
"""Reproducible entry-pipeline benchmark aggregation.

Imported timings must be measured observations with provenance. Missing values
stay missing: this module never fills, estimates, or derives stage durations.
The offline fake provider is useful for exercising the harness; it times actual
local work and is not evidence about a production provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import sys
import time
import tracemalloc
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

STAGES = (
    "upload",
    "queue",
    "cold_start",
    "model_load",
    "preprocess",
    "inference",
    "postprocess",
    "transfer",
    "local_geometry",
    "render",
    "total",
)
QUALITY_METRICS = (
    "surface_distance_mm",
    "landmark_error_mm",
    "laterality",
    "vessel_continuity",
    "thin_bone_false_clear",
)
GOALS = {
    "cached_local_p95_ms": 1_000.0,
    "warm_cloud_p95_ms": 120_000.0,
}
SCHEMA_VERSION = "entry-pipeline-benchmark-v1"


class BenchmarkInputError(ValueError):
    """Raised when input could create unsupported or misleading evidence."""


def percentile(values: Sequence[float], probability: float) -> float | None:
    """Return a linearly interpolated percentile, or None for no observations."""
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _finite_nonnegative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkInputError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise BenchmarkInputError(f"{label} must be finite and non-negative")
    return result


def _provenance(record: Mapping[str, Any]) -> Mapping[str, Any]:
    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping):
        raise BenchmarkInputError("every sample must include an object-valued provenance")
    source = provenance.get("source")
    if not isinstance(source, str) or not source.strip():
        raise BenchmarkInputError("sample provenance.source must be a non-empty string")
    basis = str(provenance.get("timing_basis", source)).lower()
    forbidden = ("fabricat", "estimate", "simulat", "synthetic_duration", "placeholder")
    if any(token in basis for token in forbidden):
        raise BenchmarkInputError(
            "timings must be observed measurements, not fabricated or estimated values"
        )
    return provenance


def validate_sample(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one observed sample without inventing fields."""
    normalized = dict(record)
    config_id = record.get("configuration_id", record.get("config_id"))
    if not isinstance(config_id, str) or not config_id:
        raise BenchmarkInputError("sample requires configuration_id")
    normalized["configuration_id"] = config_id
    normalized["provenance"] = dict(_provenance(record))

    state = record.get("run_state", record.get("temperature"))
    if state not in {"cold", "warm"}:
        raise BenchmarkInputError("sample run_state must be 'cold' or 'warm'")
    normalized["run_state"] = state

    success = record.get("success", True)
    if not isinstance(success, bool):
        raise BenchmarkInputError("sample success must be boolean")
    normalized["success"] = success
    timings = record.get("timings_ms", {})
    if not isinstance(timings, Mapping) or (success and not timings):
        raise BenchmarkInputError("successful sample timings_ms must be a non-empty object")
    unknown = set(timings) - set(STAGES)
    if unknown:
        raise BenchmarkInputError(f"unknown timing stages: {sorted(unknown)}")
    normalized["timings_ms"] = {
        stage: _finite_nonnegative(value, f"timings_ms.{stage}")
        for stage, value in timings.items()
    }
    for field in ("cost", "cost_per_case", "memory_peak_mb"):
        if field in record and record[field] is not None:
            normalized[field] = _finite_nonnegative(record[field], field)
    interactions = record.get("interaction")
    if interactions is not None:
        if not isinstance(interactions, Mapping):
            raise BenchmarkInputError("interaction must be an object")
        normalized["interaction"] = {
            key: _finite_nonnegative(value, f"interaction.{key}")
            for key, value in interactions.items()
            if key in {"clicks", "corrections", "review_time_s"} and value is not None
        }
    return normalized


def load_jsonl(path: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load samples and configuration records from a JSON Lines file."""
    source = Path(path)
    samples: list[dict[str, Any]] = []
    configurations: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise BenchmarkInputError(f"{source}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(record, dict):
                raise BenchmarkInputError(f"{source}:{line_number}: record must be an object")
            kind = record.get("record_type", "sample")
            if kind in {"configuration", "config"}:
                configurations.append(record)
            elif kind == "sample":
                try:
                    samples.append(validate_sample(record))
                except BenchmarkInputError as exc:
                    raise BenchmarkInputError(f"{source}:{line_number}: {exc}") from exc
            else:
                raise BenchmarkInputError(
                    f"{source}:{line_number}: unknown record_type {kind!r}"
                )
    return samples, configurations


def load_configuration(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSON config object/list, or configuration records from JSONL."""
    source = Path(path)
    if source.suffix.lower() == ".jsonl":
        samples, configurations = load_jsonl(source)
        if samples:
            raise BenchmarkInputError(f"{source}: configuration file contains samples")
        return configurations
    payload = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping) and "configurations" in payload:
        payload = payload["configurations"]
    if isinstance(payload, Mapping):
        payload = [payload]
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise BenchmarkInputError("configuration JSON must be an object or list of objects")
    return payload


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
    }


def _stage_summary(samples: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        stage: _distribution(
            [
                sample["timings_ms"][stage]
                for sample in samples
                if sample.get("success") and stage in sample["timings_ms"]
            ]
        )
        for stage in STAGES
    }


def _quality_values(samples: Sequence[Mapping[str, Any]], metric: str) -> list[Any]:
    values = []
    for sample in samples:
        if not sample.get("success"):
            continue
        quality = sample.get("quality")
        if isinstance(quality, Mapping) and metric in quality and quality[metric] is not None:
            values.append(quality[metric])
    return values


def _gate_rule(gates: Mapping[str, Any], metric: str) -> tuple[str, Any] | None:
    rule = gates.get(metric)
    if isinstance(rule, Mapping):
        if "max" in rule:
            return "max", rule["max"]
        if "min" in rule:
            return "min", rule["min"]
        if "equals" in rule:
            return "equals", rule["equals"]
    # Concise threshold aliases are accepted in configuration files.
    aliases = {
        "surface_distance_mm": ("max_surface_distance_mm", "max"),
        "landmark_error_mm": ("max_landmark_error_mm", "max"),
        "laterality": ("require_laterality_correct", "equals"),
        "vessel_continuity": ("min_vessel_continuity", "min"),
        "thin_bone_false_clear": ("max_thin_bone_false_clear", "max"),
    }
    alias, operator = aliases[metric]
    return (operator, gates[alias]) if alias in gates else None


def assess_quality_gates(
    samples: Sequence[Mapping[str, Any]], gates: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Assess all five required anatomical quality gates for a configuration."""
    if not isinstance(gates, Mapping):
        return {
            "passed": False,
            "reason": "quality gates not declared",
            "metrics": {},
        }
    metrics: dict[str, Any] = {}
    passed = True
    for metric in QUALITY_METRICS:
        rule = _gate_rule(gates, metric)
        values = _quality_values(samples, metric)
        if rule is None:
            metrics[metric] = {"passed": False, "reason": "gate not declared", "n": len(values)}
            passed = False
            continue
        operator, threshold = rule
        if not values:
            metrics[metric] = {
                "passed": False,
                "reason": "no observations",
                "operator": operator,
                "threshold": threshold,
                "n": 0,
            }
            passed = False
            continue
        if operator == "max":
            observed = max(_finite_nonnegative(value, f"quality.{metric}") for value in values)
            metric_passed = observed <= float(threshold)
        elif operator == "min":
            observed = min(_finite_nonnegative(value, f"quality.{metric}") for value in values)
            metric_passed = observed >= float(threshold)
        else:
            expected = threshold
            observed = all(value == expected for value in values)
            metric_passed = bool(observed)
        metrics[metric] = {
            "passed": metric_passed,
            "operator": operator,
            "threshold": threshold,
            "observed_worst_case": observed,
            "n": len(values),
        }
        passed = passed and metric_passed
    return {"passed": passed, "metrics": metrics}


def _metadata_values(samples: Sequence[Mapping[str, Any]], field: str) -> list[Any]:
    encoded: dict[str, Any] = {}
    for sample in samples:
        value = sample.get(field)
        if value is not None:
            encoded[json.dumps(value, sort_keys=True)] = value
    return [encoded[key] for key in sorted(encoded)]


def _mean_supplied(samples: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> dict[str, Any]:
    values = []
    for sample in samples:
        if not sample.get("success"):
            continue
        for field in fields:
            if sample.get(field) is not None:
                values.append(float(sample[field]))
                break
    return {"n": len(values), "mean": statistics.fmean(values) if values else None}


def summarize_configuration(
    config_id: str,
    samples: Sequence[Mapping[str, Any]],
    configuration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize one configuration, preserving missing-observation counts."""
    cold = [sample for sample in samples if sample["run_state"] == "cold"]
    warm = [sample for sample in samples if sample["run_state"] == "warm"]
    failures = [sample for sample in samples if not sample.get("success")]
    interactions: dict[str, Any] = {}
    for field in ("clicks", "corrections", "review_time_s"):
        values = [
            sample["interaction"][field]
            for sample in samples
            if sample.get("success")
            and isinstance(sample.get("interaction"), Mapping)
            and field in sample["interaction"]
        ]
        interactions[field] = _distribution(values)
    memory = [
        sample["memory_peak_mb"]
        for sample in samples
        if sample.get("success") and sample.get("memory_peak_mb") is not None
    ]
    quality_gates = (configuration or {}).get("quality_gates")
    return {
        "configuration_id": config_id,
        "configuration": dict(configuration or {}),
        "sample_count": len(samples),
        "success_count": len(samples) - len(failures),
        "failures": {
            "count": len(failures),
            "rate": len(failures) / len(samples) if samples else None,
            "failure_codes": dict(
                sorted(
                    {
                        str(code): sum(
                            1
                            for sample in failures
                            if str(sample.get("failure_code", "unspecified")) == str(code)
                        )
                        for code in {
                            sample.get("failure_code", "unspecified") for sample in failures
                        }
                    }.items()
                )
            ),
        },
        "latency_ms": {
            "all": _stage_summary(samples),
            "cold": _stage_summary(cold),
            "warm": _stage_summary(warm),
        },
        "cost_per_case": _mean_supplied(samples, ("cost_per_case", "cost")),
        "memory_peak_mb": _distribution(memory),
        "interaction": interactions,
        "scan_metadata": _metadata_values(samples, "scan_metadata"),
        "hardware_metadata": _metadata_values(samples, "hardware_metadata"),
        "quality_gate_assessment": assess_quality_gates(samples, quality_gates),
    }


def select_fastest_config(configurations: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Select minimum warm total p95 strictly among quality-gate passers."""
    eligible = []
    for result in configurations:
        if not result["quality_gate_assessment"]["passed"]:
            continue
        latency = result["latency_ms"]["warm"]["total"]["p95"]
        if latency is not None:
            eligible.append((float(latency), str(result["configuration_id"]), result))
    if not eligible:
        return None
    latency, config_id, _ = min(eligible, key=lambda item: (item[0], item[1]))
    return {
        "configuration_id": config_id,
        "selection_metric": "warm_total_p95_ms",
        "value_ms": latency,
        "quality_gates_passed": True,
    }


def _goal_assessment(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    assessments: dict[str, Any] = {}
    for result in results:
        config = result.get("configuration", {})
        deployment = config.get("deployment")
        cache = config.get("cache")
        total = result["latency_ms"]["warm"]["total"]
        if deployment == "local" and cache == "cached":
            assessments[f"{result['configuration_id']}:cached_local"] = {
                "goal_ms": GOALS["cached_local_p95_ms"],
                "observed_p95_ms": total["p95"],
                "met": total["p95"] <= GOALS["cached_local_p95_ms"]
                if total["p95"] is not None
                else None,
            }
        if deployment == "cloud":
            assessments[f"{result['configuration_id']}:warm_cloud"] = {
                "goal_ms": GOALS["warm_cloud_p95_ms"],
                "observed_p95_ms": total["p95"],
                "met": total["p95"] <= GOALS["warm_cloud_p95_ms"]
                if total["p95"] is not None
                else None,
            }
    return {
        "status": "assessed engineering goals, not performance claims",
        "targets": dict(GOALS),
        "assessments": assessments,
    }


def build_report(
    samples: Sequence[Mapping[str, Any]],
    configurations: Sequence[Mapping[str, Any]],
    input_files: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Build a deterministic report from validated observations."""
    config_by_id: dict[str, Mapping[str, Any]] = {}
    for config in configurations:
        config_id = config.get("configuration_id", config.get("config_id"))
        if not isinstance(config_id, str) or not config_id:
            raise BenchmarkInputError("configuration requires configuration_id")
        if config_id in config_by_id:
            raise BenchmarkInputError(f"duplicate configuration_id {config_id!r}")
        config_by_id[config_id] = config
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for sample in samples:
        grouped[str(sample["configuration_id"])].append(sample)
    all_ids = sorted(set(config_by_id) | set(grouped))
    results = [
        summarize_configuration(config_id, grouped[config_id], config_by_id.get(config_id))
        for config_id in all_ids
    ]
    files = []
    for input_path in input_files:
        path = Path(input_path)
        files.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    provenance = sorted(
        {
            json.dumps(sample["provenance"], sort_keys=True)
            for sample in samples
        }
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "timing_policy": (
            "Only supplied measured observations are aggregated; missing stages are null "
            "and no timings are fabricated, estimated, or derived."
        ),
        "input_provenance": {
            "files": files,
            "sample_provenance": [json.loads(item) for item in provenance],
        },
        "configurations": results,
        "fastest_passing_configuration": select_fastest_config(results),
        "engineering_goal_assessment": _goal_assessment(results),
    }


class OfflineFakeProvider:
    """Offline harness exerciser that records actual elapsed local durations."""

    def __init__(self, work_units: int = 2_000) -> None:
        if work_units < 1:
            raise ValueError("work_units must be positive")
        self.work_units = work_units

    def _measure(self, stage_index: int) -> float:
        start = time.perf_counter_ns()
        accumulator = 0
        for value in range(self.work_units + stage_index):
            accumulator = (accumulator + value * 31) % 1_000_003
        if accumulator < 0:  # pragma: no cover - keeps work observable to the interpreter
            raise AssertionError
        return (time.perf_counter_ns() - start) / 1_000_000.0

    def run(self, case_id: str, config_id: str, run_state: str) -> dict[str, Any]:
        if run_state not in {"cold", "warm"}:
            raise ValueError("run_state must be cold or warm")
        tracemalloc.start()
        total_start = time.perf_counter_ns()
        timings = {
            stage: self._measure(index)
            for index, stage in enumerate(STAGES[:-1])
        }
        timings["total"] = (time.perf_counter_ns() - total_start) / 1_000_000.0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return {
            "record_type": "sample",
            "case_id": case_id,
            "configuration_id": config_id,
            "run_state": run_state,
            "success": True,
            "timings_ms": timings,
            "memory_peak_mb": peak / (1024 * 1024),
            "hardware_metadata": {
                "platform": platform.platform(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "provenance": {
                "source": "offline_fake_provider_measured",
                "timing_basis": "perf_counter_ns observed elapsed time",
                "provider_scope": "harness exercise only; not production performance evidence",
            },
        }


def run_fake_samples(case_count: int, config_id: str, work_units: int) -> list[dict[str, Any]]:
    provider = OfflineFakeProvider(work_units)
    records = []
    for index in range(case_count):
        state = "cold" if index == 0 else "warm"
        records.append(validate_sample(provider.run(f"fake-{index + 1}", config_id, state)))
    return records


def _write_json(path: Path | None, payload: Mapping[str, Any]) -> None:
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path is None:
        sys.stdout.write(serialized)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, default=[], help="sample JSONL")
    parser.add_argument("--config", action="append", type=Path, default=[], help="JSON/JSONL config")
    parser.add_argument("--output", type=Path, help="report JSON (default: stdout)")
    parser.add_argument("--fake-cases", type=int, default=0, help="run measured offline fake cases")
    parser.add_argument("--fake-config-id", default="offline-fake")
    parser.add_argument("--fake-work-units", type=int, default=2_000)
    args = parser.parse_args(argv)
    if not args.input and args.fake_cases < 1:
        parser.error("provide --input and/or a positive --fake-cases")

    samples: list[dict[str, Any]] = []
    configurations: list[dict[str, Any]] = []
    for path in args.input:
        loaded_samples, embedded_configs = load_jsonl(path)
        samples.extend(loaded_samples)
        configurations.extend(embedded_configs)
    for path in args.config:
        configurations.extend(load_configuration(path))
    if args.fake_cases:
        samples.extend(
            run_fake_samples(args.fake_cases, args.fake_config_id, args.fake_work_units)
        )
    report = build_report(samples, configurations, [*args.input, *args.config])
    _write_json(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
