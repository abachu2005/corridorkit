import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research" / "benchmark_entry_pipeline.py"
SPEC = importlib.util.spec_from_file_location("benchmark_entry_pipeline", MODULE_PATH)
assert SPEC and SPEC.loader
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


GATES = {
    "surface_distance_mm": {"max": 1.0},
    "landmark_error_mm": {"max": 2.0},
    "laterality": {"equals": True},
    "vessel_continuity": {"min": 0.95},
    "thin_bone_false_clear": {"max": 0},
}
GOOD_QUALITY = {
    "surface_distance_mm": 0.6,
    "landmark_error_mm": 1.2,
    "laterality": True,
    "vessel_continuity": 0.99,
    "thin_bone_false_clear": 0,
}


def sample(config_id, total, state="warm", quality=None, success=True, **extra):
    record = {
        "record_type": "sample",
        "case_id": f"{config_id}-{total}",
        "configuration_id": config_id,
        "run_state": state,
        "success": success,
        "timings_ms": {
            "upload": total / 10,
            "queue": total / 10,
            "cold_start": 0 if state == "warm" else total / 10,
            "model_load": total / 10,
            "preprocess": total / 10,
            "inference": total / 5,
            "postprocess": total / 10,
            "transfer": total / 10,
            "local_geometry": total / 20,
            "render": total / 20,
            "total": total,
        },
        "quality": GOOD_QUALITY if quality is None else quality,
        "provenance": {
            "source": "instrumented_test_fixture",
            "timing_basis": "observed monotonic clock",
        },
    }
    record.update(extra)
    return benchmark.validate_sample(record)


def configuration(config_id, **extra):
    value = {"record_type": "configuration", "configuration_id": config_id}
    value.update(extra)
    return value


def test_percentiles_and_cold_warm_stage_reporting():
    samples = [
        sample(
            "cloud-a",
            100,
            state="cold",
            cost_per_case=1.0,
            memory_peak_mb=100,
            scan_metadata={"modality": "CT", "shape": [10, 20, 30]},
            hardware_metadata={"accelerator": "fake"},
            interaction={"clicks": 2, "corrections": 1, "review_time_s": 8},
        ),
        sample(
            "cloud-a",
            20,
            cost_per_case=3.0,
            memory_peak_mb=200,
            interaction={"clicks": 4, "corrections": 0, "review_time_s": 12},
        ),
        sample("cloud-a", 40),
        sample("cloud-a", 0, success=False, failure_code="provider_timeout"),
    ]
    report = benchmark.build_report(
        samples,
        [configuration("cloud-a", deployment="cloud", quality_gates=GATES)],
    )
    result = report["configurations"][0]

    assert result["latency_ms"]["warm"]["total"] == {"n": 2, "p50": 30.0, "p95": 39.0}
    assert result["latency_ms"]["cold"]["total"]["p95"] == 100.0
    assert set(result["latency_ms"]["all"]) == set(benchmark.STAGES)
    assert result["failures"] == {
        "count": 1,
        "rate": 0.25,
        "failure_codes": {"provider_timeout": 1},
    }
    assert result["cost_per_case"] == {"n": 2, "mean": 2.0}
    assert result["memory_peak_mb"] == {"n": 2, "p50": 150.0, "p95": 195.0}
    assert result["interaction"]["clicks"] == {"n": 2, "p50": 3.0, "p95": 3.9}
    assert result["interaction"]["corrections"]["p50"] == 0.5
    assert result["interaction"]["review_time_s"]["p95"] == 11.8
    assert result["scan_metadata"] == [{"modality": "CT", "shape": [10, 20, 30]}]
    assert result["hardware_metadata"] == [{"accelerator": "fake"}]
    goal = report["engineering_goal_assessment"]
    assert goal["status"] == "assessed engineering goals, not performance claims"
    assert goal["assessments"]["cloud-a:warm_cloud"]["met"] is True


def test_missing_timings_remain_missing_and_provenance_is_reported():
    record = sample("partial", 10)
    record["timings_ms"] = {"inference": 3.5}
    report = benchmark.build_report(
        [record],
        [configuration("partial", quality_gates=GATES)],
    )
    result = report["configurations"][0]

    assert result["latency_ms"]["warm"]["inference"] == {"n": 1, "p50": 3.5, "p95": 3.5}
    assert result["latency_ms"]["warm"]["total"] == {"n": 0, "p50": None, "p95": None}
    assert "never" not in report["timing_policy"].lower()
    assert report["input_provenance"]["sample_provenance"] == [
        {
            "source": "instrumented_test_fixture",
            "timing_basis": "observed monotonic clock",
        }
    ]


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value.pop("provenance"), "provenance"),
        (
            lambda value: value.update(
                provenance={"source": "fixture", "timing_basis": "estimated duration"}
            ),
            "not fabricated",
        ),
        (lambda value: value["timings_ms"].update(inference=-1), "non-negative"),
        (lambda value: value["timings_ms"].update(mystery=1), "unknown timing"),
    ],
)
def test_invalid_or_unmeasured_timing_records_are_rejected(mutation, message):
    record = sample("invalid", 10)
    mutation(record)
    with pytest.raises(benchmark.BenchmarkInputError, match=message):
        benchmark.validate_sample(record)


def test_fastest_selection_excludes_quality_failure_and_missing_gate():
    bad_quality = dict(GOOD_QUALITY, laterality=False)
    samples = [
        sample("fast-bad", 5, quality=bad_quality),
        sample("medium-undeclared", 10),
        sample("slow-good", 20),
    ]
    report = benchmark.build_report(
        samples,
        [
            configuration("fast-bad", quality_gates=GATES),
            configuration("medium-undeclared"),
            configuration("slow-good", quality_gates=GATES),
        ],
    )
    by_id = {item["configuration_id"]: item for item in report["configurations"]}

    assert not by_id["fast-bad"]["quality_gate_assessment"]["passed"]
    assert (
        by_id["fast-bad"]["quality_gate_assessment"]["metrics"]["laterality"]["passed"]
        is False
    )
    assert not by_id["medium-undeclared"]["quality_gate_assessment"]["passed"]
    assert report["fastest_passing_configuration"] == {
        "configuration_id": "slow-good",
        "selection_metric": "warm_total_p95_ms",
        "value_ms": 20.0,
        "quality_gates_passed": True,
    }


def test_all_five_quality_gates_must_have_observations():
    incomplete = dict(GOOD_QUALITY)
    incomplete.pop("thin_bone_false_clear")
    assessment = benchmark.assess_quality_gates(
        [sample("incomplete", 10, quality=incomplete)],
        GATES,
    )

    assert assessment["passed"] is False
    assert assessment["metrics"]["thin_bone_false_clear"]["reason"] == "no observations"


def test_jsonl_cli_records_input_hash_and_does_not_select_failed_quality(tmp_path):
    source = tmp_path / "samples.jsonl"
    output = tmp_path / "report.json"
    records = [
        configuration(
            "local",
            deployment="local",
            cache="cached",
            quality_gates=GATES,
        ),
        sample("local", 900),
    ]
    source.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

    subprocess.run(
        [sys.executable, str(MODULE_PATH), "--input", str(source), "--output", str(output)],
        check=True,
    )
    report = json.loads(output.read_text(encoding="utf-8"))

    assert report["fastest_passing_configuration"]["configuration_id"] == "local"
    assert report["engineering_goal_assessment"]["assessments"]["local:cached_local"][
        "met"
    ] is True
    assert len(report["input_provenance"]["files"][0]["sha256"]) == 64


def test_offline_fake_provider_reports_measured_stages_without_quality_claims():
    records = benchmark.run_fake_samples(2, "offline", 10)
    report = benchmark.build_report(records, [])

    assert len(records) == 2
    assert records[0]["run_state"] == "cold"
    assert records[1]["run_state"] == "warm"
    assert set(records[0]["timings_ms"]) == set(benchmark.STAGES)
    assert all(value >= 0 for value in records[0]["timings_ms"].values())
    assert records[0]["provenance"]["source"] == "offline_fake_provider_measured"
    assert report["fastest_passing_configuration"] is None
