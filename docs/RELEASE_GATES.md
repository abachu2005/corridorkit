# Research and translation gates

This file distinguishes completed software verification and co-author review
from future independent anatomical validation and clinician usability testing.
Software-publication criteria are tracked separately in
[`SOFTWARE_RELEASE_CHECKLIST.md`](SOFTWARE_RELEASE_CHECKLIST.md).
Passing computational tests does not establish anatomical or operative validity.

## Co-author review

Anand V. Germanwala, a skull-base neurosurgeon and co-author, reviewed the
software's corridor model, research workflow, and interpretation of geometric
results, informing the methodology and presentation. This is the review
reported in Section 3.1 of the manuscript; it is distinct from the formal
independent anatomical validation and clinician usability studies listed below.

## Implemented and exercised

- Segmented synthetic planning fixture: complete voxel target, explicitly
  synthetic CT-like context, two apertures and analytical protected spheres.
  Physical projected aperture rims, target category contours/3D surfaces,
  dimensioned selected capsule, path-plane diameter display, and approach
  isolation modes are implemented. Native evidence:
  `research/planning-evidence/segmented-phantom-final`.
  Optional sampled shaft occupancy preserves gaps rather than constructing
  a convex envelope. These are visualization/phantom checks, not anatomical
  or clinical validation; shell/air-space phantom geometry is display-only.

- Standalone Apache-2.0 Python package, published as
  [CorridorKit v0.3.0](https://github.com/abachu2005/corridorkit/releases/tag/v0.3.0);
  no changes to AutoHijdra.
- Finite insertion capsules, aperture constraints, target-directed witnesses,
  budgeted adaptive sampling, portal offsets, conservative voxel collision,
  approach-specific reviewed bone removal, separate union/pair feasibility.
- Missing/unsupported geometry suppresses measurements; no-path findings retain
  explicit sampling incompleteness. Nonzero shared-origin overlap exemptions
  are unsupported rather than ignoring collisions.
- Native-grid RAS-mm CT import, explicit conventional DICOM selection, linked
  orthogonal slices, label surfaces, finite witnesses, undoable configuration
  and brush editing, stale/cancel handling, review-state persistence, hashes,
  JSON/CSV/screenshot exports.
- MRI registration proposals, direction-explicit transforms, landmark/overlay
  review with input hashes, reviewed resampling and MRI overlay.
- NasalSeg v2: all 130 IDs accounted for, 118 integrity-eligible cases evaluated,
  12 geometry mismatches excluded. See separate frozen records under
  `research/results/airspace-v1` and `production-public-v3`.
- Production engine ran ray/finite-instrument and simultaneous comparisons on
  118 **computational airspace cases**, with separate per-case records. The
  openings/targets are not surgical EEA/CTM openings or tumors.
- Source/wheel build and installed-wheel CLI smoke outside the source tree;
  installed-wheel smoke reused system dependencies.
- Scan-centered planning now includes selectable unconditional feasible paths,
  dashed orthogonal projections distinguished from solid slice intersections,
  an affine-aware path-aligned CT plane, and category-based target coverage.
  The comparison panel and exports distinguish EEA-only, TM-only, both,
  not-reached-at-this-sampling and unavailable points. Full versus sparse target
  mask import is an explicit choice, not silent volume suppression.
  See `docs/PLANNING_WORKFLOW.md` and native real-CT execution evidence in
  `research/planning-evidence`. This does not establish operative necessity.

## Evidence boundaries and future validation

- **Formal independent anatomical validation remains future work.**
  The co-author model/workflow review above does not establish case-level
  anatomical accuracy. NasalSeg has no tumor, carotid,
  cranial-nerve or operative corridor reference. No automatically manufactured
  target or portal can substitute for review. Full EEA/CTM anatomical comparison
  on this cohort is not established.
- **Independent human desktop acceptance is not complete.** Automated offscreen
  interactions and native Qt/VTK screenshot smoke are available; they are not
  clinician usability testing. Initial macOS VTK expose-loop starvation was
  reproduced and fixed with paced repainting.
- **Native macOS runtime smoke now passes locally.** Approved generated output
  and regenerable caches were removed to recover the 5 GiB staging reserve.
  The frozen `.app` passed all eight runtime checks outside the checkout with
  Python/Conda environment overrides removed, plus ad-hoc signature verification.
  See `research/native-bundle-evidence/frozen-preflight`; its stricter overall
  verifier remained failed because that first build lacked the source manifest.
  **Final manifest-bound verification passed** in
  `research/native-bundle-evidence/frozen-verified`: all eight runtime checks,
  strict/deep ad-hoc signature verification and exact source-manifest match.
  A transient VTK dylib signing error in the intervening build was repaired with
  local ad-hoc signing; its failed record is retained in `frozen-final`.
  A local compressed DMG was checksum-verified, mounted read-only, and its
  contained app passed the same verifier (`native-bundle-evidence/dmg-verified`).
  No Developer-ID-signed/notarized distribution or verified native Windows/Linux
  package exists.
  **Those frozen-bundle records predate the scan-centered planning changes.**
  The current source workflow is separately native-tested. The old bundle/DMG
  must not be described as containing these features; no current `dist/` bundle
  is present. Rebuilding requires the packaging script's 5 GiB staging reserve.
  The local `.command` demo launcher runs current source, not a frozen binary.
- **Exact-tag cross-platform CI passed for v0.3.0.**
  [All eight jobs passed](https://github.com/abachu2005/corridorkit/actions/runs/37847343247),
  including core tests on Ubuntu, macOS, and Windows with Python 3.11/3.12.
  Native desktop and Slicer 5.12.3 integration were separately verified on
  macOS; this does not establish native GUI acceptance on every platform.
  See [`evidence/corridorkit-v0.3.0-verification.json`](evidence/corridorkit-v0.3.0-verification.json).
- **Physical/operative error tolerances and reference validation are absent.**
  Computational tolerances and independent phantom checks are not surgical
  measurement tolerances. No endoscopic optics, handle access, deformation,
  staged debulking or safe-resection probability is modeled.
- **Sparse targets are not volume-validated.** Full voxel-center masks have
  physical cell weights; subsampled masks suppress volume estimates.
- **No parameter tuning for surgical performance has occurred.** The public
  grid is frozen and descriptive; P identifiers proxy subjects but distinct-ID
  biological independence is unverified.
- **General protected-geometry angular quadrature remains unresolved.** The initial finest
  tested grid still had 11.368% relative error for a small off-axis target.
  The negative result is retained in `research/numerical-benchmark`; sampled
  solid angle must not be described as uniformly accurate.
  A subsequent standalone adaptive experiment reduced that error to 1.649%
  but missed its prespecified 1% target and entirely missed a narrow-cap stress
  case. It was not substituted into production. See `research/refined-angular`.
  Geometry-aware production integration now passes the unobstructed analytic
  subgate: 82/82 comparisons, including the original narrow-cap case, randomized
  caps, rotations, finite-length annuli and overlapping targets. Maximum observed
  relative error was 0.0001204123%, below the prespecified 1% threshold, reproduced
  in `research/geometric-angular-independent-replay`. This is not a universal
  error bound. Protected-structure cases still use coarse grid quadrature with an
  explicit warning; they are not covered by this passed subgate.

## Optional boundary refinement

Voxel geometry can opt into `refine_near_boundary=true`. Coarse blocked queries
then use a bounded, exact-distance reference against closed affine voxel cells,
up to `max_refinement_cells` (default 128). Budget exhaustion preserves the
conservative blockage; FOV abstention cannot be overridden. Refined positive
clearance is capped at 0.1 mm and floating-point guarded. This is a voxel-cell
model, not anatomical mesh validation. The full cohort run retains the coarse
default; optional refinement has separate synthetic/sheared-cell regression tests.

Additional independent box-optimization evidence is in
`research/voxel-refinement-v5` and `research/voxel-refinement-independent-replay`.
The primary run includes 530 synthetic queries and 120 queries on three existing
public computational cases; no false-clear classification, clearance-bound
violation, FOV-policy change or budget-fallback change was observed. This is not
a new full-cohort surgical validation. Earlier failed experimental records are
retained. See `docs/HUMAN_ACCEPTANCE.md` for the unexecuted independent review
checklist; no reviewer approval has been manufactured.

The unchanged bounded query protocol was then applied to **all 118 eligible
cases**, with no parameter tuning: 4,720 public queries, 2,164 recovered clear
queries independently checked against the candidate-cell box oracle, and zero
observed false-clear/bound/policy violations. All 1,190 public budget-exhausted
queries retained conservative blockage. See `research/voxel-refinement-full-cohort`.
This is 40 queries per case, not exhaustive trajectory validation; the public
oracle shares broad-phase candidate selection and does not independently prove
that selection complete.
Full-cohort replay passed with byte-identical synthetic and public query records.

These remaining research and translation gates do not imply that the published
software release is unfinished. They delimit future anatomical, operative, and
independent usability claims.
