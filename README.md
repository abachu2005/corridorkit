# CorridorKit

CorridorKit is an Apache-2.0 research software package for
surgeon-supervised geometric exploration of rigid skull-base approach
corridors in physical image coordinates (millimeters). It samples finite rigid
instruments through declared portals and reports reach, insertion depth,
clearance, target-set coverage, retained path witnesses, and explicit
abstention states.

Version 0.3.0 uses `corridorkit` for the repository, Python distribution,
import package, and command-line tool, and `corridorkit-gui` for the desktop.
See the [v0.3.0 release](https://github.com/abachu2005/corridorkit/releases/tag/v0.3.0).

## Quickstart

Generate and analyze a deterministic case without patient data:

```bash
python -m pip install -e '.[desktop,test]' build
corridorkit synthetic case.json
corridorkit analyze case.json result.json
corridorkit doctor
```

For image-linked review, install the packaged
[3D Slicer module](slicer/README.md), load a CT, place one target, and select
**Plan Corridors**. A managed external Python runtime runs TotalSegmentator and
the finite-instrument engine on the workstation; CT data are not uploaded.
The first setup/model use requires network access for pinned Python packages
and model weights. Later execution can reuse the verified local cache. An
optional authenticated Azure backend is retained for engineering benchmarks,
but is not required by public users.

Predicted masks and modeled paths require review. Missing cranial nerves,
cavernous-sinus contents, dura, or other critical anatomy remains visibly
unavailable; a modeled feasible path is not a safe surgical route.

## Scope and evidence

The numerical geometry engine contains no anatomy thresholds or landmark
logic; the approach labels, TotalSegmentator adapter, entry-proposal workflow,
and public-data evaluation are cranial-specific. This is research software,
not autonomous planning software, a validated navigation system, or a treatment
recommendation.

This work used only synthetic data and publicly available, deidentified data,
and did not involve human subjects research. It involved no recruitment,
private records, chart review, outcomes, or identity linkage. See the
[SoftwareX manuscript](paper/softwarex.md), [validation summary](docs/VALIDATION.md),
and [source-release checklist](docs/SOFTWARE_RELEASE_CHECKLIST.md).

The public `research/` directory contains reproducibility scripts and protocols;
`docs/evidence/` contains tracked verification summaries. Full historical
benchmark outputs and caches remain in the local development checkout.
`infra/azure/` contains optional historical benchmark
infrastructure. Neither is required for the public workstation workflow, and
cloud benchmarking is not part of the SoftwareX evaluation.

## Install and verify

```bash
python packaging/verify_source_release.py --desktop --tests
```

Use Python 3.11 or newer. The full test collection includes GUI modules, so the
command above installs desktop dependencies and runs each GUI-related module
in a fresh process; numerical tests are grouped. This avoids the known
combined Qt-suite hang. It is offscreen verification, not native or human
acceptance. A core-only install can use `.[test]` and run the verifier without
`--desktop --tests`; this does not test the full suite.

Launch the desktop with:

```bash
corridorkit-gui
```

The dark PACS-style workspace contains linked anatomical RAS-mm slices,
an optional interactive VTK 3-D view, approach and instrument controls, undo
and redo, asynchronous analysis, and checksummed export. Open the bundled
synthetic demonstration from the landing page for a data-free walkthrough.
Changing an analysis parameter invalidates the displayed result until it is
recalculated.

See `docs/USER_GUIDE.md` for the complete workflow and
`docs/VALIDATION.md` for the current evidence boundary.

Recorded engineering evidence: full 130-case public accounting (118 computational cases,
12 geometry exclusions), independent reference and production-engine runs,
and native macOS Qt/VTK smoke. Historical build evidence is not proof that an
old application bundle contains current source. This is **not surgical
validation**. Protected-anatomy angular accuracy, expert anatomy review and
human usability acceptance remain open in `docs/RELEASE_GATES.md`. Those
clinical/translation gates are separate from the bounded software-publication
criteria in `docs/SOFTWARE_RELEASE_CHECKLIST.md`; neither checklist is entirely
complete. Do not interpret public computational openings as anatomical EEA or
Caldwell–Luc routes. Frozen evidence is retained under `research/` in the
development checkout, but is not bundled with the lightweight source release.

The JSON export embeds the schema/software version, normalized configuration,
source checksum, and result checksum. Synthetic data generation is local and
deterministic; no network data is used.

## Model scope

- Target geometry: physical-coordinate point clouds, including mask-derived
  voxel-center samples with affine-preserving conversion.
- Protected anatomy: analytic spheres and affine voxel masks (`.npy`, `.npz`,
  or medical images supported by nibabel). Meshes intentionally abstain.
- Voxel-mask collision is evaluated in patient coordinates. Foreground cells
  are conservatively bounded, centerlines are sampled from physical voxel
  spacing, and interpolation error is subtracted from clearance. A shaft that
  requires geometry outside the mask field of view causes abstention.
- Portal: a finite circular disk. The oblique elliptical shaft footprint must
  fit inside the aperture and travel through its forward face.
- Instrument: a finite rigid shaft with `radius_mm`, maximum `length_mm`,
  and independent `tip_working_radius_mm`. Collision/aperture checks
  conservatively use the larger of shaft and working-tip radii.
- A target point is reached when the tip/working point can be inserted to its
  axial depth and its perpendicular distance is no greater than
  `tip_working_radius_mm + target_tolerance_mm`. Shaft radius is never target
  tolerance. Collision is checked only over the required insertion segment.
- Unknown or out-of-field anatomy causes abstention. Explicit unknown-anatomy
  exploration remains incomplete and suppresses quantitative claims.
- EEA and transmaxillary approaches can be summarized separately, as a union,
  intersection, and incremental contribution without double counting.
- Simultaneous pairs retain each instrument's reached target set and enforce
  minimum angular separation plus finite capsule-to-capsule clearance.
  Same-portal shafts collide at their shared base by default.
  Nonzero legacy `shared_portal_overlap_mm` is unsupported: collisions at a
  shared origin cannot be exempted.
- The desktop imports CT DICOM/NIfTI/NRRD, uses orthogonal anatomical slices,
  supports aligned masks and physical brush corrections, and records review
  state. Required structures are never inferred from the five NasalSeg labels.

## Result interpretation

Every sampled trajectory is retained in JSON with its direction, reached target
indices, insertion depths, clearance, and rejection reason. Approach summaries
include witness polar/azimuth angles relative to the configured nominal
direction and a solid-angle measurement in steradians when supported. For
unobstructed, supported center-entry target configurations, geometry-aware
interval-union integration is independent of witness sampling. With protected
structures, coarse grid quadrature uses spherical-cap Voronoi area weights;
extra target-directed/adaptive witnesses do not acquire quadrature weight.
Its general 1% accuracy gate is unresolved, and narrow feasible components can
be missed. Increase `polar_steps` and `azimuth_steps` and check convergence;
convergence alone does not certify accuracy or safety. Unsupported integration
or incomplete anatomy suppresses the affected measurement.

## Current-source distribution

```bash
python packaging/verify_source_release.py --desktop --tests --build \
  --output dist/source-release-new --report dist/source-release-new-report.json
python packaging/verify_wheel.py dist/source-release-new/corridorkit-0.3.0-py3-none-any.whl
```

Use a fresh output/report path for each attempt. The verifier builds from a
source-only staging allowlist, verifies archive contents against current files,
and records SHA-256 hashes. Source archives contain code, tests and text docs,
not public CT data, local sessions, screenshots or benchmark result archives.
Building may fetch the small PEP 517 build backend. The wheel smoke reuses
installed system dependencies: it is not a clean-machine installation test.
No command above publishes packages or builds a frozen application.

## Coordinate convention

All geometry uses an explicit affine mapping array indices `(i, j, k)` to
physical points in millimeters. Image loaders explicitly convert declared
LPS/RAS and spatial units into RAS mm. Domain cases require a single declared
frame across their target/image/masks; mismatches are rejected, not aligned silently.

## License

Apache-2.0.
