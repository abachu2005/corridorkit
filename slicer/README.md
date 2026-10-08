# Anatomical EEA–CTM comparison in 3D Slicer

This is the focused replacement workflow for the standalone interaction demo.
It uses Slicer's existing image loading, linked slices, segmentation editor,
transforms, markups and scene persistence. It does not install or replace
Slicer's Python packages.

## Current scope

User-defined EEA and contralateral maxillary entries share one skull-base target
and one two-point petrous ICA reference axis. The module reports:

- Acute axial projected angle to the ICA axis for each approach.
- EEA angle minus CTM angle (a geometric difference, not clinical superiority).
- Full 3-D straight-line entry-to-target working distance.
- Optional signed difference between user-defined lateral limits along a
  user-defined medial-to-lateral axis.

Two finite-diameter shafts are displayed in 3-D with true slice intersections.
These are illustrations of intended segments, not collision-tested instruments.
The separate **Compare segmented target access…** action runs the existing
finite-instrument engine against explicitly selected segmentations.
No default anatomical coordinates or fabricated skull-base target are supplied.
Incomplete/out-of-volume landmarks invalidate the report and hide the shafts.
The selected image/landmark references and diameter persist in a Slicer scene.

## Install from the checkout

1. Install 3D Slicer from https://download.slicer.org/ if not already available.
2. In **Edit → Application Settings → Modules → Additional module paths**, add
   `slicer/SkullBaseComparison` from this repository. Restart Slicer.
3. Open **Skull-base approach comparison** under **IGT**.
4. Load a suitable CT and its segmentation using Slicer's normal Add Data tools.
   Preserve `.seg.nrrd` files as segmentation nodes, including overlapping layers.
5. Select the CT. **Show full CT in linked views** fits the supplied image extent;
   it cannot restore anatomy absent from an acquisition.
6. Create/place the two entries, common target and two-point ICA axis using the
   node selectors and placement buttons. Inspect all three slice orientations.
7. Review measured differences. Optional lateral-limit inputs must all be defined.
   These are manually identified limits, not a computed accessibility map.
8. Save a `.mrb` scene to retain the images, labels, landmarks and settings.
   Export JSON separately for coordinates, definitions and measured values.

## Install the packaged Slicer 5.12 extension

Build and independently verify the deterministic archive from the repository
root:

```sh
python packaging/build_slicer_package.py
python packaging/verify_slicer_package.py \
  dist/SkullBaseCorridor-Slicer-5.12-0.2.0.zip
```

Extract the archive to a permanent directory. In Slicer 5.12, open
**Edit → Application Settings → Modules → Additional module paths**, add the
extracted `SkullBaseCorridor/slicer/SkullBaseComparison` directory, and restart
Slicer. Do not add the ZIP itself.

The package includes a pinned wheelhouse, its exact SHA-256 manifest, and a
managed-runtime launcher at `SkullBaseCorridor/slicer/runtime/bin/python`.
The first planning run verifies every wheel and installs them into an immutable,
manifest-keyed per-user cache. It does not modify Slicer's Python packages and
keeps CT inference on the local workstation by default. Initial model-weight
retrieval requires network access; later runs can reuse the local model and
content-hash caches. Set `SKULLBASE_BOOTSTRAP_PYTHON` only if
`python3` is not Python 3.11 or newer. The archive contains no endpoint token,
patient data, build cache, or provisioned virtual environment.

Public atlas data are an anatomical reference, not an individual tumor scan.
Check the atlas inspection report and its separate license before using/sharing.
No atlas-transferred segmentation should be treated as reviewed patient anatomy.

The acquired originals are in `data-cache/skullbase-atlas/`. From Slicer's
Python console, use `import runpy` followed by
`runpy.run_path("/absolute/path/to/skullbase-corridor/slicer/load_anatomical_case.py")`.
This loads the original CT and independent segmentation components without
flattening their overlaps or creating entry/target landmarks.

**Inspection finding:** the atlas ICA annotation spans only 7.5 mm
craniocaudally; it is not a complete petrous/paraclival vessel mask.
It also lacks explicit petroclival targets and operative openings.
See [the exact coverage report](../docs/ATLAS_INSPECTION.md).
Do not use this partial mask to claim artery clearance along an entire route.

## Segmented access comparison

Install the core dependencies in a separate Python 3.11+ environment with
`python -m pip install -e .` from this checkout. Set the dialog's external Python
path to that environment's interpreter (or set `SKULLBASE_PYTHON` before launch).
Slicer's bundled scientific packages are not modified.

1. Establish the anatomical landmarks above, then open **Compare segmented target access…**.
2. Select a target segment and a union segment containing all protected structures
   relevant to this model. Preserve individual originals; make the union using
   Segment Editor's logical operations on a copy.
3. Optionally select bone and separate EEA/CTM bone-removal segments. These are
   explicit user-defined virtual resections, not automatic corridor creation.
   Removal never deletes protected anatomy.
4. Specify aperture diameter and instrument length. The shaft diameter is inherited
   from the main module. Apertures face the common target landmark; this simplified
   bridge does not model independently oriented internal openings.
5. Supply a reviewer identity and declare completeness only after appropriate
   anatomical review. Missing critical anatomy results in **unavailable** coverage.
   Selecting the UW atlas alone does not justify checking this declaration.
6. Run into an empty output directory. Inputs are copied onto the CT grid in XYZ
   order with an explicit world-RAS affine. Harden parent transforms beforehand.
7. Inspect the resulting coverage segmentation in linked CT views and 3-D:
   blue EEA-only, orange CTM-only, green both, red not reached at this sampling,
   gray unavailable. Each overlay is a named **snapshot**, not a live result.
   Hide old snapshots before comparing a newly edited configuration.

The output directory retains mask arrays, the request, engine log, and JSON/CSV
reports. Save the Slicer scene separately. The bounded bridge accepts up to 2,000
target voxels; it rejects larger targets rather than silently subsampling them.
Use an explicitly documented research ROI or the standalone engine for larger
studies. A sampled failure is not proof of anatomical inaccessibility.

## Verification

Pure measurements have headless tests in `tests/test_anatomical_measurements.py`.
Run `Slicer --no-splash --python-script slicer/verify_in_slicer.py` for native
integration verification. Syntax checks alone do not establish Slicer compatibility.
Native integration **passed on 2026-10-07**, using Slicer 5.12.4 on macOS 15.4
under Rosetta. Verified landmark invalidation, rotated anisotropic full-grid
exports, all five coverage categories, external engine execution, the real
asynchronous dialog, and scene-reference save/reopen. Tests found and fixed
missing landmark-deletion observers and two PythonQt compatibility errors.
`slicer/verify_atlas_in_slicer.py` also passed: original 367 × 449 × 304 CT and
all 14 segmentation components loaded and rendered. Evidence is retained in
`research/slicer-verification/`. This is software integration evidence, not
expert anatomy review or clinical validation. Do not use `--no-main-window`
for these tests: linked views require Slicer's main-window layout manager.

On this workstation Slicer is installed at
`~/Applications/Slicer-5.12.4.app`. To open the module without changing global
module-path settings:

```sh
SKULLBASE_PYTHON=/opt/anaconda3/bin/python3 \
 ~/Applications/Slicer-5.12.4.app/Contents/MacOS/Slicer \
 --additional-module-paths "$PWD/slicer/SkullBaseComparison"
```

## What remains before an operative-corridor demonstration

- Verify the specific scan covers the entry, petrous/paraclival ICA, petrous apex
  and target, with adequate spatial resolution and reviewed landmarks.
- Define each approach's internal openings and intended bone removal.
- Review protected segmentations and approach-specific removal masks before using
  the implemented world-coordinate bridge.
- Report constraint-specific obstruction and incomplete anatomy. Do not confuse
  absent labels with absence of vessels/nerves.
- Separate camera visibility, instrument reach and simultaneous-instrument fit.

The module deliberately makes **no** safety or automatic anatomical-access claim.
Its coverage output describes only sampled access to the supplied target in the
configured model. It is not a validated operative planner.

## Method references

- [Anatomical Limits of the Endoscopic Contralateral Transmaxillary Approach](https://pmc.ncbi.nlm.nih.gov/articles/PMC8824622/):
  petrous ICA reference, lateral reach and approach-specific preparation.
- [Combined endonasal transclival and CTM approach](https://www.mdpi.com/2077-0383/13/9/2713):
  distinct endoscope and working-instrument corridors.
- [Automated atlas-based segmentation](https://pmc.ncbi.nlm.nih.gov/articles/PMC8317429/):
  public anatomical template and limitations of transferred small-structure labels.

Our fixed-target projected-angle definition is explicit and reproducible, but
does not by itself reproduce the published maximal-access measurement protocol.
