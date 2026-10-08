"""Generate a concise evidence-linked report from actual benchmark outputs."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = json.loads((args.directory / "summary.json").read_text())
    manifest = json.loads((args.directory / "frozen-manifest.json").read_text())
    with gzip.open(args.directory / "per-case.jsonl.gz", "rt") as stream:
        records = [json.loads(line) for line in stream]
    clipping = sum(any(label["possible_label_clipping"] for label in row.get("audit", {}).get("label_coverage", []))
                   for row in records)
    missing_labels = Counter(label.get("reason") for row in records for label in row.get("labels", [])
                             if label["status"] == "excluded")
    excluded = [row["case_id"] for row in records if row["status"] != "evaluated"]
    comparison = summary["geometry_comparison"]
    lines = [
        "# Public scan-based airspace computational benchmark: actual results",
        "",
        "**This is not an anatomical surgical comparison or surgical validation.**",
        "The production corridor engine is not evaluated by this independent backend.",
        "Expert anatomical review remains **incomplete**. No carotids or petroclival",
        "targets were invented; the targets are verified source-label interior centers.",
        "",
        "## Source and freeze",
        f"- Source: {summary['source']['url']}",
        f"- Archive bytes: {summary['source']['bytes']:,}; license: {summary['source']['license']}.",
        f"- Published and verified MD5: `{summary['source']['md5']}`.",
        f"- SHA-256: `{summary['source']['sha256']}`.",
        f"- Manifest SHA-256: `{summary['frozen_manifest_sha256']}`.",
        f"- Pre-case-load freeze timestamp: `{manifest['started_utc']}`.",
        "- Split, all 9 radius/length configurations, four scenarios, and source-code",
        "  hashes are recorded in `frozen-manifest.json`. There was no outcome-based tuning.",
        "",
        "## Actual execution",
        f"- Source IDs: {summary['n_source_identifiers']}; case statuses: `{summary['case_status_counts']}`.",
        f"- Exclusion reasons: `{summary['exclusion_reason_counts']}`.",
        f"- Excluded IDs: {', '.join(excluded)}.",
        f"- Scenario-segment checks: {comparison['scenario_segment_count']:,}.",
        f"- Grid classifications: {comparison['grid_classification_count']:,}.",
        f"- Classification mismatches: {comparison['classification_mismatches']};",
        f"  false-feasible versus exhaustive sphere-model reference: {comparison['false_feasible']}.",
        f"- Maximum absolute **capped** clearance difference: {comparison['max_capped_clearance_absolute_error_mm']:.8g} mm.",
        f"- Measured benchmark runtime: {summary['runtime_seconds']:.3f} seconds",
        "  (excludes download, reproduction, and clinical review; not a performance guarantee).",
        f"- Cases with at least one label touching scan boundary: {clipping}/{len(records)}.",
        f"- Label-level target-selection exclusions: `{dict(missing_labels)}`.",
        "",
        "Clipping does not automatically exclude interior computational targets; capsule",
        "FOV containment is checked. It does block treating these crops as complete anatomy.",
        "The 2 mm capped-clearance agreement reflects numerical implementation agreement,",
        "not subvoxel anatomical accuracy. Positive capped distances are lower bounds",
        "once the cap is reached. Boundary spheres are conservative geometric surrogates.",
        "",
        "## Subject-weighted descriptive contrast",
        "Baseline radius 1 mm minus radius 0 mm at finite length 30 mm; values are",
        "demonstrated coverage fractions. Repeat trajectories are averaged within",
        "each source subject ID. Bootstrap intervals resample subjects, not trajectories.",
    ]
    for partition, values in summary["paired_contrast_by_partition"].items():
        lines.append(
            f"- {partition}: n={values['n_subjects']}, mean difference="
            f"{values['mean_paired_difference']:.6f}, percentile bootstrap 95% interval "
            f"{values['bootstrap_95_percent_interval']}."
        )
    lines += [
        "",
        "These are descriptive computational comparisons; increasing radius cannot",
        "improve free-space reach under this model. They establish no surgical advantage.",
        "Source P IDs are the available grouping unit; independent patient identity",
        "across source IDs is unverified. Excluded subjects do not enter paired effects.",
        "",
        "## Scenario status accounting",
        "Registration scenarios move obstacles ±1 mm in RAS X. The segmentation",
        "scenario inflates boundary spheres by 1 mm. Frequencies are sensitivity",
        "results, not clinical probabilities. Every radius/length/scenario cell is",
        "in the JSON; below are pooled engineering counts, **not independent subjects**.",
    ]
    scenario_counts = {}
    for key, value in summary["grid_status_counts"].items():
        scenario, _, status = key.split("/")
        scenario_counts.setdefault(scenario, Counter())[status] += value
    for name, counts in scenario_counts.items():
        lines.append(f"- {name}: {dict(counts)}.")
    lines += [
        "",
        "## Artifacts and reproduction",
        f"- Results directory: `{args.directory}`.",
        "- `integrity-audit.json`: per-pair SHA-256, RAS geometry, intensity and clipping fields.",
        "- `per-case.jsonl.gz`: all case statuses, coordinates, target-label provenance,",
        "  skipped self-segments, scenarios, reference distances, grid classifications.",
        "- `summary.json`: all case exclusions, grid status counts, paired differences.",
        "- `frozen-manifest.json`: source, configuration, split, software and code hashes.",
        "",
        "```bash",
        "python research/tools/download_nasalseg.py",
        "python research/tools/run_airspace_benchmark.py --output research/results/airspace-v1",
        "python research/tools/run_airspace_benchmark.py --output research/results/airspace-v1-reproduction",
        "python research/tools/verify_airspace_results.py research/results/airspace-v1 \\",
        "  --compare research/results/airspace-v1-reproduction",
        "PYTHONPATH=src python -m pytest tests/test_data_audit.py tests/test_analysis.py tests/test_analysis_airspace.py -q",
        "```",
        "",
        "Choose new output names if these frozen directories already exist. The trusted",
        "224 MB archive remains in ignored `research/data-cache`; only one pair is",
        "temporarily extracted and cleanup deletes only that run's owned temporary",
        "directory. No image NRRDs are present in the result artifacts.",
        "",
        "## Remaining gates",
        "- Expert anatomical review, full craniofacial coverage, surgical targets,",
        "  vessels and operative openings: **not established**.",
        "- Production engine and desktop integration: **not tested by this benchmark**.",
        "- True voxel-box/mesh geometry and insertion-path validation: **not established**;",
        "  reference agreement is limited to the explicitly modeled boundary spheres.",
        "- Physical calibration, segmentation accuracy and clinical registration error:",
        "  **unknown**; the assumed perturbations do not resolve those uncertainties.",
        "- No clinical safety, outcome, or patient-specific planning validation.",
        "",
    ]
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(str(args.output))


if __name__ == "__main__":
    main()
