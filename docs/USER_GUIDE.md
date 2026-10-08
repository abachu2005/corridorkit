# CorridorKit user guide

These instructions target CorridorKit v0.3.0 from
[the CorridorKit repository](https://github.com/abachu2005/corridorkit).
The Python package/import name and CLI are `corridorkit`.

## Start

```bash
python -m pip install -e '.[desktop,test]'
corridorkit-gui
```

Open a version 1.1 corridor case or select **Open synthetic demonstration**.
The synthetic case is the distributed tutorial and requires no patient data.

If a public image omits spatial units, inspect its documentation and make an
explicit, recorded assumption before import; do not silently accept it:

```bash
corridorkit convert-volume source.nrrd ct-mm.nii.gz --assume-spatial-unit mm
```

Use the same conversion for its aligned label map. This records a provenance
sidecar and preserves the declared anatomical orientation.

## Workspace

- Import CT accepts NIfTI/NRRD; Import DICOM requires an explicit conventional
  CT series selection. Images are displayed in RAS millimetres, with orthogonal
  axial/coronal/sagittal resampling. Ambiguous units or transforms are rejected.
- The 3-D panel displays affine-transformed CT bone/label surfaces, targets,
  finite nominal instruments, and computed witnesses when native VTK is available.
- Blue identifies EEA and orange identifies transmaxillary geometry. These
  colors identify approaches, not safety states.
- Edit portal, instrument, tip-working, and sampling parameters in the right
  panel. An edit immediately invalidates old results. Undo and redo operate on
  these versioned configurations.

Select **Run geometric analysis** to calculate sampled access. Results report:

- reached target samples under the configured model;
- estimated feasible directional solid angle;
- insertion depth of a selected witness (insertion span is a separate field);
- minimum modeled clearance;
- union, overlap, and incremental coverage;
- simultaneous pair feasibility under the stated separation/collision model.

“Not found” means no path was found at the configured sampling resolution. It
is not a mathematical proof of inaccessibility. “Abstained” indicates missing,
unsupported, or out-of-field anatomy under conservative settings.

## Save and export

**Save** writes the editable case. **Export analysis** writes a checksummed
envelope containing the normalized configuration, software version, analysis
options, external-file hashes, and complete trajectory results, plus CSV.
Local saves also retain review events and invalidate anatomy approval on edits.
External data is referenced, not embedded; keep referenced files available.
Source hashes are checked before/after analysis and again at export.

## Anatomy review and editing

Importing a CT creates visibly unreviewed target/portal placeholders and unknown
required critical anatomy. It does **not** detect a tumor or opening. Use the
pick-mode control to place a target or either portal on linked slices; numerical
portal/instrument controls and undo/redo retain explicit configuration changes.

Import mask requires exact native-grid alignment and a foreground label. Assign
its anatomical identity explicitly. Bone cannot substitute for an unknown
critical structure. Inspect all required anatomy; the application cannot tell
whether your required-structure list is medically complete.

Paint/erase applies a physical-mm spherical brush to the selected label without
overwriting other labels. Undo/redo works for strokes. Save/apply edited mask
writes a new NIfTI and assigns it to the selected target/protected structure;
analysis remains disabled until the correction is applied.

Configure bone-removal mask accepts only an explicitly reviewed subset of a
named bone mask, separately for each approach. It never removes critical masks.
Removal is a static preparation assumption, not a simulated operation.

Threshold suggestions can be generated with:

```bash
corridorkit suggest-ct ct.nii.gz suggestions/
```

These depend on calibrated HU, miss thin bone, include artifacts/exterior air,
and always require correction/review. No automatic carotid/tumor model is used.
Subsampled targets deliberately omit volume estimates; a sparse sample must not
be presented as a validated tumor-volume measurement.

## MRI registration review

```bash
corridorkit register-mri ct.nii.gz mr.nii.gz ct-to-mr.tfm
corridorkit review-registration ct-to-mr.registration.json landmarks.json \
  ReviewerName 2.0 --overlays-reviewed
corridorkit resample-mri ct-to-mr.registration.json reviewed-mr.nii.gz
```

Choose the landmark tolerance *before* review; `2.0` above is an example, not a
validated clinical tolerance. Landmarks JSON contains matched `fixed_lps` and
`moving_lps` arrays in mm, at least three noncollinear points. The saved transform
maps fixed CT LPS points to moving MRI LPS points for resampling. Image/transform
changes invalidate review. Optimizer convergence alone does not approve registration.

## Public-data audit

```bash
corridorkit audit-dataset images labels audit.json \
  --source-version DATASET_VERSION
```

This verifies basic image/label integrity. It does not qualify a case for
surgical planning. Review all entry, target, and protected anatomy before using
an imported case.

## Numerical analyses

```bash
corridorkit convergence case.json convergence.json
corridorkit sensitivity case.json sensitivity.json \
  --repeats 200 --portal-sigma-mm 1
```

Sensitivity intervals describe the declared perturbation experiment. They are
not safety probabilities or clinical confidence intervals.
