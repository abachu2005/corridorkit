# Software manuscript scaffold

Working title: **skullbase-corridor: reproducible physical-space geometry for
surgeon-supervised exploration of rigid skull-base approach corridors**

Status: substantive draft scaffold, not a submitted manuscript or a statement
that publication acceptance criteria have been met. Author names, affiliations,
contributions, funding, conflicts, institutional determinations and archival
software identifiers must be supplied and verified by the responsible people.
No authorship, approval, novelty priority or clinical efficacy is asserted here.

## Abstract draft

**Purpose.** Provide inspectable research software for comparing the geometric
reach of finite rigid instruments through user-defined skull-base openings.
The objective is reproducible geometric exploration under surgeon supervision,
not automated selection of surgery, prediction of safe resection or outcome
assessment.

**Software and methods.** A Python engine represents target samples, protected
geometry, circular portals and finite instruments in a declared physical image
frame. It records sampled trajectories, reached target indices, required
insertion depths, clearances and abstention reasons. A desktop workspace
supports anatomical slices, path-aligned inspection, reviewed inputs and
approach-specific coverage. Verification uses deterministic analytical
fixtures, independent geometric references, existing public computational
examples and automated desktop interactions.

**Evidence and limits.** Retained public-data records account for 130 image/label
pairs: 118 were geometry-eligible and 12 were excluded. They test execution on
computational airspace configurations, not annotated surgical corridors.
Analytical angular and voxel-cell checks provide bounded numerical evidence;
protected-geometry angular accuracy and independent clinician acceptance remain
unresolved. Historical evidence must be linked to its frozen source and not
automatically attributed to a later release.

**Conclusion.** The package exposes a reproducible geometric model for research
inspection. Its outputs describe modeled access only. No private patient data,
clinical outcomes, surgical superiority or clinical safety are evaluated in this
software-paper scope.

## 1. Problem and intended scope

Corridor descriptions must distinguish an infinitely thin ray, a finite-width
instrument, alternative routes to a target and simultaneous instrument access.
Combining them can turn an apparently reachable target into an ambiguous
measurement. This software makes those assumptions explicit and retains
witness geometry so that a reviewer can inspect the basis of each result.

The intended user is a technically supported surgeon or researcher reviewing
geometric configurations. Target, portal and protected-anatomy definitions are
inputs requiring review, not clinical ground truth inferred by the software.
“Surgeon-supervised” is a scope restriction and intended workflow; it is not
evidence of a completed surgeon study. The proposed article is a software and
engineering report without private patient data or patient outcomes.

Before submission, add a verified related-work section addressing existing
planning, surgical-access and medical-image software. Compare actual scope,
input requirements and output semantics. No “first,” superiority or
state-of-the-art claim is supported by the current materials.

## 2. Software methods

### 2.1 Architecture and reproducibility

The `src/skullbase_corridor` package separates typed domain configurations,
geometry, analysis, image I/O, application services, desktop views and exports.
The CLI supports synthetic case generation, analysis and environment reporting.
The desktop provides interactive inspection using Qt and optional VTK.
`pyproject.toml` declares Python >=3.11 and release version 0.2.1; the domain
schema is separately versioned (currently 1.1). A schema version is not a claim
of algorithm validation.

The checkout also includes a scripted 3D Slicer module. It measures acute
axial projected approach angles relative to a user-defined petrous ICA axis,
straight-line working distances and optional signed lateral-limit differences.
These landmarks are manually supplied, not automatically inferred operative
anatomy. Illustrated shafts are distinct from collision-tested engine results.

An external-process bridge exports selected target, protected and bone segments
onto the full CT grid, converts Slicer's KJI array order to XYZ, and records the
world-RAS affine. Explicit reviewed approach-specific removal masks affect only
bone. Incomplete protected anatomy forces abstention. The returned sampled
coverage partition is displayed as a saved Slicer segmentation snapshot.
The bounded bridge limits target masks to 2,000 foreground voxels and rejects
larger targets rather than silently replacing them with sparse volume estimates.
Its aperture normals point toward the common landmark; independently oriented
internal surgical openings are not represented by this bridge.

JSON exports contain normalized configuration and source/result checksums.
Review-state persistence and input fingerprints support traceability and
stale-result detection, but hashes are neither anonymization nor proof that
the inputs are anatomically correct. Release verification must retain the
source manifest, commands, interpreter/dependency versions and artifact hashes.

### 2.2 Coordinates and input representations

Geometry is expressed in millimetres in an explicitly declared physical frame.
An affine maps voxel indices to physical positions. Image loaders normalize
declared spatial units and RAS/LPS conventions into RAS mm; incompatible domain
frames are rejected rather than silently aligned. CT can be imported from
supported DICOM, NIfTI and NRRD inputs. Registration and resampling are explicit
review steps rather than assumptions of correct multimodal alignment.

Targets are point samples or mask-derived voxel centers. Complete voxel
representations can carry physical cell weights from the affine determinant.
Sparse samples support count-based coverage, not inferred tumor-volume
percentages. Protected geometry includes analytical spheres and affine voxel
masks; unsupported geometry, including the unsupported mesh input path, must
not be treated as empty space.

### 2.3 Finite-instrument reach and aperture constraints

For entry point `e`, unit direction `d` and target point `x`, the required
axial insertion is `t = (x-e)·d`; perpendicular distance is
`||(x-e)-t d||`. A target is a reach candidate only when the insertion is
nonnegative, does not exceed the instrument length and the perpendicular
distance is within working-tip radius plus the configured target tolerance.
Numerical implementation tolerances apply at boundaries.

The collision model is a finite capsule from `e` to `e+t d`. Shaft radius
does not inflate target tolerance. Collision and aperture checks conservatively
use the maximum of shaft and working-tip radii over the insertion. This is not
a manufacturer-specific instrument model or an articulated/deformable tool.

A portal is a finite circular disk with a declared forward normal. The
oblique shaft footprint must fit the aperture, accounting conservatively for
entry offset, and the direction must cross the forward face. Collision is
evaluated for the required insertion segment rather than an infinite ray.
Target-directed and budgeted adaptive directions can add reach witnesses to
the base angular grid; neither makes the search exhaustive.

### 2.4 Protected geometry and abstention

Sphere clearance is analytical. Voxel masks represent closed affine cells:
the coarse backend uses conservative physical-space bounds and interpolation
guards. Optional near-boundary refinement checks candidate affine cells with
a finite cell budget. Exhausting that budget preserves conservative blockage;
it must not convert unknown geometry into clearance. A shaft requiring
geometry outside the mask field of view triggers the out-of-field policy.

Missing, unknown or unsupported protected anatomy can cause abstention.
Explicit exploratory allowance of unknown anatomy leaves results incomplete
and suppresses affected quantitative claims. These policies reduce certain
modeling errors; they cannot detect all missing vessels, segmentation errors,
incorrect portals or unmodeled tissues. Conditional results must remain
distinguishable from unconditional witnesses.

### 2.5 Approach comparison and simultaneous access

The engine retains reached target-index sets separately for each approach.
Union, intersection and incremental coverage are set operations, preventing
double counting. “Not reached” means no evaluated path reached a point; it is
not proof of physical or operative inaccessibility. EEA/transmaxillary labels
describe configured approaches and do not establish that the configured
opening is anatomically valid or that Caldwell–Luc is necessary.

Simultaneous comparisons additionally enforce angular separation and finite
capsule-to-capsule clearance while preserving each instrument's target set.
Alternative-path union coverage is not simultaneous feasibility. Coincident
portal origins collide by default; a legacy shared-origin collision exemption
is unsupported.

### 2.6 Angular measurements

Witness polar/azimuth components describe selected directions relative to the
nominal axis. Solid angle is a separate measurement in steradians. Supported
unobstructed center-entry cases use geometry-aware interval-union integration;
the retained validation protocol covers at most 32 target points and specified
analytic geometries. Unsupported or nonconverged measurements are suppressed,
and numerical error estimates are not certified bounds.

Protected-structure cases use coarse center-entry grid quadrature with
spherical-cap Voronoi area weights. Additional target-directed or adaptive
witnesses receive no quadrature weight. A general 1% angular-accuracy claim
is not established; narrow feasible regions may be missed. Report sampling,
integration applicability, termination and convergence behavior with any
quantitative use.

### 2.7 Supervised desktop workflow

Users inspect images and input geometry, configure openings/instruments and
run analysis. Linked axial/coronal/sagittal slices and a path-aligned plane
support inspection of the selected finite witness. Dashed projections are
distinguished from actual slice intersections. Coverage categories distinguish
approach-only, shared, sampled-unreached and unavailable targets. Synthetic
planning phantoms permit demonstration without any patient data.

Configuration changes invalidate prior measurements. Undo, persistence and
export checks support review; they do not substitute for anatomical acceptance.
Optional sampled shaft occupancy is a visualization of evaluated capsules,
not a filled safe-access envelope or an additional collision test.

## 3. Verification design and evidence ledger

The following are **recorded historical engineering results**, not new runs
performed for this draft. Source hashes and protocols in each evidence
directory define their applicability. The source-release verifier separately
checks the current checkout; a passing current test suite does not replay
every historical benchmark.

1. **Core geometry and numerical stress tests.**
   `tests/test_engine_contract.py`, `tests/test_geometry.py`,
   `tests/test_geometry_repairs.py`, `tests/test_geometric_angular.py` and
   `research/numerical-benchmark/summary.json` exercise finite insertion,
   aperture, target tolerance, abstention and numerical references.
   The historical sheared-cell benchmark recorded zero false-feasible
   classifications in 99 queries but 41 conservative-only blockages.
   Its finest grid still produced 11.368% relative angular error for an
   off-axis target. Retain this negative result.
2. **Restricted angular subgate.**
   `research/geometric-angular-independent-replay/results.json` and its
   protocol/frozen sources record 82/82 unobstructed analytical comparisons
   below the prespecified 1% relative-error threshold (maximum observed
   0.0001204123%). This is an observed result on specified fixtures, not an
   error guarantee for protected anatomy or arbitrary target configurations.
3. **Voxel refinement.**
   `research/voxel-refinement-v5/summary.json` and
   `research/voxel-refinement-independent-replay/summary.json` contain 530
   synthetic and 120 public-case queries. The extended
   `research/voxel-refinement-full-cohort/summary.json` contains 4,720 public
   queries across 118 eligible cases, zero observed false-clear/bound/policy
   violations, 2,164 recovered-clear queries and 1,190 conservative budget
   fallbacks. The replay retains matching record hashes. This is 40 queries
   per case, not exhaustive access validation; the public oracle shares
   candidate selection and does not independently prove its completeness.
4. **Public-data execution.**
   `research/nasalseg-v2-audit.json`,
   `research/AIRSPACE_BENCHMARK_RESULTS.md` and
   `research/results/production-public-v3/verification.json` account for all
   130 pairs, 118 eligible configurations and 12 geometry exclusions. The
   production verifier records 12,310 feasible witnesses and checks hashes,
   partitions, set identities, pair constraints and statistics. The inputs
   use computational targets/openings, not tumor or operative reference
   annotations. Distinct IDs do not independently prove biological independence.
5. **Desktop and packaging.**
   `tests/test_planning_workflow.py`, `tests/test_anatomical_views.py`,
   `tests/test_coverage_comparison.py` and related tests exercise workflow
   behavior. `research/planning-evidence/segmented-phantom-final/report.json`
   records successful programmatic native synthetic demonstration, including
   path selection and stale-path clearing. Historical frozen-bundle checks in
   `research/native-bundle-evidence` predate later planning changes; they are
   not current-source binary evidence. Independent human acceptance and
   hosted cross-platform CI are not established.

For manuscript results, attach a fresh source-release report and installed-wheel
smoke record, quote exact passed/failed/skipped test counts, and identify the
tested platform. Do not replace failures with averages or describe skipped
checks as successful verification. Automated offscreen GUI tests run in
separate fresh processes because combined Qt tests have a known hang; this
workaround is not proof that the underlying lifecycle issue is fixed.

## 4. Data, ethics and availability

The proposed report uses locally generated synthetic fixtures and existing
public, deidentified data only. It does not require private patient records,
new patient recruitment, chart review, outcomes or linkage to identities.
Do not add such material merely to broaden this software manuscript.

Public deidentification is not an institutional ethics determination. The
responsible institution may still require a non-human-subjects research
determination or other review, depending on jurisdiction, data and intended
use. No IRB approval, exemption, consent waiver or non-human-subjects
determination has been verified here. Before submission, obtain and quote
the applicable determination accurately, or explain the institution's
documented requirement; never manufacture an approval identifier.

The retained source manifest identifies NasalSeg v2 as Zenodo record
[13893419](https://zenodo.org/records/13893419), with declared CC-BY-4.0 terms
and archive SHA-256
`60c6facf843685802c39e4adff4a05c081c1c4b6175c9cb573745c55abb0fa6a`.
Verify the current upstream record, attribution/citation requirements and
per-file terms before use or redistribution; do not silently mix v1 and v2.
License terms and public access do not authorize reidentification or clinical
claims. Do not imply independently verified deidentification from filename
checks alone.

Code is Apache-2.0; dependencies retain their own terms (see
`docs/DEPENDENCIES_AND_DATA.md`). The lightweight release contains source,
tests and text documentation, but not public CT archives, local sessions,
screenshots or frozen benchmark data. The evidence paths above refer to the
development checkout: they need a separately reviewed, versioned archival
supplement before a reader can reproduce the full paper. Review identifiers,
paths and visible image annotations before sharing any artifact.

## 5. Limitations and permissible conclusions

The model omits tissue deformation, dissection planes, endoscopic optics,
handle access, hemostasis, reconstruction and staged debulking. It neither
infers complete neurovascular anatomy nor measures safe resectability.
Input uncertainty, sparse target sampling, finite trajectory sampling and
conservative voxel approximations limit interpretation. Zero observed errors
on bounded tests are not proof of zero risk.

No cadaveric, tracked-instrument, operative-video or clinical-outcome reference
has established clinical accuracy. No independent surgeon usability study or
approach-superiority comparison is reported. Source distribution and software
publication can be assessed within this bounded scope; patient treatment and
clinical-performance claims require a separate study and approvals.

## 6. Submission items still requiring responsible review

- Select a suitable software venue and verify its actual submission requirements.
- Supply verified authorship/contributions, affiliations, funding and conflicts.
- Verify related work and bibliographic references; the paths above are an
  evidence ledger, not a completed reference bibliography.
- Record the applicable institutional determination and public-data terms.
- Archive a fixed source version and approved evidence supplement with durable
  identifiers; neither publication nor a DOI is claimed by this draft.
- Bind reported tests and demonstrations to that version and disclose skips,
  remaining numerical limitations and environment reuse.
- Prepare figures from synthetic fixtures first; any public-image figure needs
  provenance, licensing and disclosure review.

Completion criteria for a software release are in
`docs/SOFTWARE_RELEASE_CHECKLIST.md`. Clinical and native-distribution gates
remain separately documented in `docs/RELEASE_GATES.md`.
