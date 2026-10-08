# Public scan-based airspace computational benchmark: actual results

**This is not an anatomical surgical comparison or surgical validation.**
The production corridor engine is not evaluated by this independent backend.
Expert anatomical review remains **incomplete**. No carotids or petroclival
targets were invented; the targets are verified source-label interior centers.

## Source and freeze
- Source: https://zenodo.org/api/records/13893419/files/NasalSeg.zip/content
- Archive bytes: 224,005,800; license: CC-BY-4.0.
- Published and verified MD5: `ba83df47974d907798542101fa43ac7d`.
- SHA-256: `60c6facf843685802c39e4adff4a05c081c1c4b6175c9cb573745c55abb0fa6a`.
- Manifest SHA-256: `5ea3e38fde61b63561239008b5b46f3c0c5ce9eef6942d31c49cbd0218881c35`.
- Pre-case-load freeze timestamp: `2026-10-01T21:19:43.171522+00:00`.
- Split, all 9 radius/length configurations, four scenarios, and source-code
  hashes are recorded in `frozen-manifest.json`. There was no outcome-based tuning.

## Actual execution
- Source IDs: 130; case statuses: `{'evaluated': 118, 'excluded': 12}`.
- Exclusion reasons: `{'image_label_physical_geometry_mismatch': 12}`.
- Excluded IDs: P025, P039, P042, P074, P089, P096, P100, P101, P105, P108, P118, P129.
- Scenario-segment checks: 13,192.
- Grid classifications: 118,728.
- Classification mismatches: 0;
  false-feasible versus exhaustive sphere-model reference: 0.
- Maximum absolute **capped** clearance difference: 7.7715612e-15 mm.
- Measured benchmark runtime: 194.792 seconds
  (excludes download, reproduction, and clinical review; not a performance guarantee).
- Cases with at least one label touching scan boundary: 127/130.
- Label-level target-selection exclusions: `{'fewer_than_three_2mm_interior_centers': 1}`.

Clipping does not automatically exclude interior computational targets; capsule
FOV containment is checked. It does block treating these crops as complete anatomy.
The 2 mm capped-clearance agreement reflects numerical implementation agreement,
not subvoxel anatomical accuracy. Positive capped distances are lower bounds
once the cap is reached. Boundary spheres are conservative geometric surrogates.

## Subject-weighted descriptive contrast
Baseline radius 1 mm minus radius 0 mm at finite length 30 mm; values are
demonstrated coverage fractions. Repeat trajectories are averaged within
each source subject ID. Bootstrap intervals resample subjects, not trajectories.
- development: n=80, mean difference=-0.020982, percentile bootstrap 95% interval [-0.026785714285714284, -0.01517857142857143].
- evaluation: n=18, mean difference=-0.023810, percentile bootstrap 95% interval [-0.03968253968253968, -0.00992063492063492].
- validation: n=20, mean difference=-0.029058, percentile bootstrap 95% interval [-0.039772727272727265, -0.01834415584415584].

These are descriptive computational comparisons; increasing radius cannot
improve free-space reach under this model. They establish no surgical advantage.
Source P IDs are the available grouping unit; independent patient identity
across source IDs is unverified. Excluded subjects do not enter paired effects.

## Scenario status accounting
Registration scenarios move obstacles ±1 mm in RAS X. The segmentation
scenario inflates boundary spheres by 1 mm. Frequencies are sensitivity
results, not clinical probabilities. Every radius/length/scenario cell is
in the JSON; below are pooled engineering counts, **not independent subjects**.
- baseline: {'abstained': 0, 'blocked': 26025, 'reached': 3657}.
- registration_x_minus_1mm: {'abstained': 0, 'blocked': 27033, 'reached': 2649}.
- registration_x_plus_1mm: {'abstained': 0, 'blocked': 27070, 'reached': 2612}.
- segmentation_boundary_plus_1mm: {'abstained': 0, 'blocked': 28426, 'reached': 1256}.

## Artifacts and reproduction
- Results directory: `research/results/airspace-v1`.
- `integrity-audit.json`: per-pair SHA-256, RAS geometry, intensity and clipping fields.
- `per-case.jsonl.gz`: all case statuses, coordinates, target-label provenance,
  skipped self-segments, scenarios, reference distances, grid classifications.
- `summary.json`: all case exclusions, grid status counts, paired differences.
- `frozen-manifest.json`: source, configuration, split, software and code hashes.

```bash
python research/tools/download_nasalseg.py
python research/tools/run_airspace_benchmark.py --output research/results/airspace-v1
python research/tools/run_airspace_benchmark.py --output research/results/airspace-v1-reproduction
python research/tools/verify_airspace_results.py research/results/airspace-v1 \
  --compare research/results/airspace-v1-reproduction
PYTHONPATH=src python -m pytest tests/test_data_audit.py tests/test_analysis.py tests/test_analysis_airspace.py -q
```

Choose new output names if these frozen directories already exist. The trusted
224 MB archive remains in ignored `research/data-cache`; only one pair is
temporarily extracted and cleanup deletes only that run's owned temporary
directory. No image NRRDs are present in the result artifacts.

## Remaining gates
- Expert anatomical review, full craniofacial coverage, surgical targets,
  vessels and operative openings: **not established**.
- Production engine and desktop integration: **not tested by this benchmark**.
- True voxel-box/mesh geometry and insertion-path validation: **not established**;
  reference agreement is limited to the explicitly modeled boundary spheres.
- Physical calibration, segmentation accuracy and clinical registration error:
  **unknown**; the assumed perturbations do not resolve those uncertainties.
- No clinical safety, outcome, or patient-specific planning validation.
