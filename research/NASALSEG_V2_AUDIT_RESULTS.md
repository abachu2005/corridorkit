# NasalSeg v2 automated audit results

Historical schema-1 integrity audit only. It is **not corridor evaluation**.
The original NRRD affine handling used LPS without an explicit RAS conversion;
within-format matching counts remain useful but should not be used for mixed-format
coordinate interpretation. The strengthened RAS audit and actual scan-based
computational benchmark are now documented in
[AIRSPACE_BENCHMARK_RESULTS.md](AIRSPACE_BENCHMARK_RESULTS.md), with frozen
per-case records under `research/results/airspace-v1`.

Audit date: 2026-10-01

Source: Zenodo record `13893419`, CC BY 4.0. The downloaded archive checksum is
recorded by Zenodo as MD5 `ba83df47974d907798542101fa43ac7d`.

## Results

- 130 image files and 130 label files were present.
- 130 pairs were identified by subject identifier.
- 118 pairs passed finite-value, expected-label, matching-shape, and matching
  physical-geometry checks.
- 12 pairs failed the physical-geometry check: P025, P039, P042, P074, P089,
  P096, P100, P101, P105, P108, P118, and P129.
- All inspected labels used only expected values 0–5.
- Passing pairs used approximately 0.586 × 0.586 × 1.5 mm spacing and met the
  protocol's automated ≤2 mm spacing indicator.

The failures are retained rather than silently realigned. Examples include a
reversed slice direction with a shifted origin (P025) and an image/label
through-plane spacing mismatch (P039). Each discrepancy requires source review
before any corrected derivative is used.

The machine-readable case-level report is
`research/nasalseg-v2-audit.json`. Image archives remain in the ignored local
`research/data-cache` directory and are not redistributed.

## Interpretation

This audit establishes only basic data and coordinate integrity for 118 pairs.
NasalSeg provides nasal/maxillary air-space labels, not complete operative
anatomy, tumor, carotid/optic structures, approach openings, or access ground
truth. No case is yet qualified for patient-specific surgical planning.
