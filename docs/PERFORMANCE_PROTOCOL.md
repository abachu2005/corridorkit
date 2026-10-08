# Entry-pipeline performance protocol

This protocol defines reproducible engineering measurements for an anatomy
entry pipeline. It does not establish clinical performance, diagnostic
accuracy, surgical safety, or suitability for patient care.

## Measurement policy

Run `research/benchmark_entry_pipeline.py` against newline-delimited JSON
records. Each sample must contain `configuration_id`, `run_state` (`cold` or
`warm`), an object-valued `timings_ms`, and provenance with a non-empty
`source`. The provenance `timing_basis` must describe an observed measurement
(for example, a monotonic-clock span or provider timestamp), not an estimate,
simulation, placeholder, or fabricated duration.

The harness accepts only these millisecond stages:

1. `upload`
2. `queue`
3. `cold_start`
4. `model_load`
5. `preprocess`
6. `inference`
7. `postprocess`
8. `transfer`
9. `local_geometry`
10. `render`
11. `total`

Instrument stages at their true boundaries. Do not infer a missing stage by
subtracting other stages from `total`, and do not force stage sums to equal
`total`: overlapping provider spans and clock boundaries can make that
misleading. Missing measurements remain `null` with `n: 0` in summaries.
Successful observations receive linearly interpolated p50 and p95 summaries;
failures are counted separately and excluded from latency, cost, memory, and
interaction distributions.

Pin the dataset/case manifest, model and application revisions, provider
region, instance type, accelerator/runtime versions, and pipeline parameters.
Record scan metadata sufficient to stratify results (for example modality,
voxel spacing, dimensions, and byte size) without including patient
identifiers. Record hardware metadata with each observation. Preserve the
input JSONL and report together; the report includes SHA-256 hashes of input
files and distinct sample-provenance records.

## Cold, warm, cache, and repetitions

Declare cold and warm runs before execution:

- **Cold** means the relevant process/container and model/cache state have
  been cleared according to the provider-specific procedure.
- **Warm** means that initialization has completed and the declared cache state
  is stable.
- `cache: "cached"` is a configuration declaration, independent of
  `run_state`; document exactly what is cached.

Use the same case order across configurations or a seeded, recorded
randomization. Do not discard retries. Record unsuccessful attempts with
`success: false` and a stable `failure_code`. Choose repetition counts before
looking at results, and report the sample count for every distribution.

## JSONL records

A file may interleave configuration and sample records:

```json
{"record_type":"configuration","configuration_id":"cloud-a","deployment":"cloud","quality_gates":{"surface_distance_mm":{"max":1.0},"landmark_error_mm":{"max":2.0},"laterality":{"equals":true},"vessel_continuity":{"min":0.95},"thin_bone_false_clear":{"max":0}}}
{"record_type":"sample","case_id":"case-001","configuration_id":"cloud-a","run_state":"warm","success":true,"timings_ms":{"upload":120.1,"queue":8.2,"cold_start":0.0,"model_load":4.1,"preprocess":20.3,"inference":1500.4,"postprocess":40.2,"transfer":88.0,"local_geometry":25.0,"render":12.0,"total":1818.3},"cost_per_case":0.04,"memory_peak_mb":2048,"scan_metadata":{"modality":"CT","dimensions":[512,512,280],"spacing_mm":[0.5,0.5,0.6]},"hardware_metadata":{"provider":"example","region":"example-region","accelerator":"example"},"interaction":{"clicks":3,"corrections":1,"review_time_s":45},"quality":{"surface_distance_mm":0.8,"landmark_error_mm":1.5,"laterality":true,"vessel_continuity":0.98,"thin_bone_false_clear":0},"provenance":{"source":"instrumented-run-2026-10-07","timing_basis":"observed monotonic clock and provider timestamps"}}
```

`cost` or `cost_per_case`, `memory_peak_mb`, `scan_metadata`,
`hardware_metadata`, and the interaction fields `clicks`, `corrections`, and
`review_time_s` are summarized only when supplied. A failure can omit
timings. Numeric observations must be finite and non-negative.

Configuration can instead be supplied as JSON using `--config`; accepted
shapes are one configuration object, a list, or
`{"configurations": [...]}`. Configuration JSONL uses only configuration
records.

## Anatomical quality gates and selection

Declare thresholds before timing results are examined. Every candidate must
have a declared rule and observations for all five gates:

- surface distance (`surface_distance_mm`; worst case must be at or below its
  maximum);
- landmark error (`landmark_error_mm`; worst case must be at or below its
  maximum);
- laterality (`laterality`; every observation must equal the declared value,
  normally `true`);
- vessel continuity (`vessel_continuity`; worst case must be at or above its
  minimum);
- thin-bone false-clear (`thin_bone_false_clear`; worst case must be at or
  below its maximum, normally zero).

The report selects the smallest warm `total` p95 only among configurations
that pass every declared gate. A faster configuration with failed, undeclared,
or unobserved gates is ineligible. If no passing configuration has a measured
warm total, no winner is selected. This ordering prevents speed from
substituting for anatomical quality.

Thresholds belong to the study protocol and must be justified against the
reference annotation and intended use. The harness enforces the declared rules
but does not certify their clinical adequacy.

## Engineering targets

The report can assess these predeclared engineering goals:

- cached local pipeline: p95 `total` no greater than 1,000 ms;
- warm cloud pipeline: p95 `total` no greater than 120,000 ms.

These are goals, not claims. They are assessed only where configuration
metadata declares `deployment: "local", cache: "cached"` or
`deployment: "cloud"`. A `null` assessment means no qualifying measured warm
total was supplied. Meeting either target does not imply quality-gate passage
or clinical acceptability.

## Commands

Aggregate measured records:

```bash
python research/benchmark_entry_pipeline.py \
  --input benchmark-samples.jsonl \
  --config benchmark-config.json \
  --output benchmark-report.json
```

Multiple `--input` and `--config` arguments are accepted. The output is sorted
and deterministic for identical inputs except when newly measured fake-provider
records are included.

Exercise collection and reporting offline:

```bash
python research/benchmark_entry_pipeline.py \
  --fake-cases 5 \
  --fake-work-units 2000 \
  --output fake-provider-report.json
```

The fake provider uses `perf_counter_ns` around actual local CPU work and
reports peak traced Python memory. It makes no network requests and does not
emit configured or synthetic durations. Its records intentionally contain no
anatomical quality observations, so they cannot win configuration selection.
They validate harness mechanics only and are not production performance
evidence.

Verify the implementation:

```bash
python -m pytest tests/test_entry_benchmark.py
```
