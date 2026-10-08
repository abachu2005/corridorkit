# NasalSeg eligibility audit

## Source

- Dataset: NasalSeg
- Concept DOI: <https://doi.org/10.5281/zenodo.12177180>
- Original full-image release: <https://zenodo.org/records/12177181>
- License recorded by Zenodo: CC BY 4.0
- Expected labels: background, bilateral maxillary sinuses, bilateral nasal
  cavities, and nasopharynx

The original release contains four image archives and one label archive. The
later v2 record contains one substantially smaller archive and must not be
assumed equivalent without a content audit.

## Fixed engineering checks

Run:

```bash
skullbase-corridor audit-dataset extracted/images extracted/labels audit.json \
  --source-version 12177181
```

The audit records file hashes, dimensions, spacing, finite values, image/label
geometry agreement, label values, and a thin-slice indicator. An image is
`engineering_eligible` only when its paired geometry and labels pass these
checks.

Schema 2 additionally enforces exact case identifiers after documented suffix
removal (never sorted-list zip pairing). Duplicate IDs are errors; unmatched
files and unsampled pairs are reported explicitly. Labels must be integral.
NRRD LPS coordinates are converted to RAS to agree with NIfTI. Geometry
comparison uses absolute tolerance only. Full RAS affines, physical FOV corners
and bounds, label boundary-contact/clipping indicators, and intensity
percentiles are retained. Intensities are not assumed to be calibrated HU.
Header spacing is not an estimate of image/registration accuracy.

For the small cropped v2 dataset, use the streaming source and evaluation tools:

```bash
python research/tools/download_nasalseg.py
python research/tools/run_airspace_benchmark.py --output research/results/airspace-v1
```

The separate frozen [airspace protocol](AIRSPACE_BENCHMARK_PROTOCOL.md) tests
controlled computational geometry on source labels. It is not an anatomical
surgical comparison, and does not complete the expert review gate below.

## What this does not establish

NasalSeg does not provide tumor, complete skull-base carotid/optic anatomy,
operative openings, instrument paths, or surgical-access reference labels.
Passing the automated audit does not establish intact entry-to-target anatomy.
Every candidate scan still requires blinded expert review of:

1. nostrils and anterior nasal aperture;
2. anterior maxillary wall/canine-fossa region;
3. pterygoid and sphenoid/clival coverage;
4. artifacts or prior surgery;
5. whether the selected target and all protected anatomy are represented.

Cases failing required field of view remain excluded rather than treating
cropped space as unobstructed.

## Split policy

All targets, sides, perturbations, and repeated measurements derived from one
subject remain in the same partition. Development parameters are frozen before
the held-out evaluation. The public cohort supports engineering and anatomical
variability analysis, not clinical resection claims.
