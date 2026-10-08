# Engineering validation statement

Version: 0.2.0 research software release; clinical release gates remain open.

## Established

- Physical-coordinate target conversion preserves arbitrary invertible
  affines and anisotropic voxel volume.
- Sphere/capsule primitives are checked against analytical and independently
  sampled references.
- Protected voxel masks use conservative physical-space cell bounds and
  interpolation guards.
- Portal, length, target-tip, collision, set-union, and simultaneous-instrument
  behavior have automated boundary tests.
- Unknown, unsupported, unavailable, and out-of-field protected anatomy cause
  abstention under the default configuration.
- Sampling convergence and seeded input-sensitivity workflows are
  reproducible.
- Desktop import, calculation, stale-result invalidation, undo, persistence,
  and export pass offscreen interaction tests.

The exact command and environment record for the frozen synthetic benchmark is
stored in `research/validation_report.json`.

## Public-data audit

The NasalSeg v2 archive contained 130 paired filenames. Automated checks found
118 image/label pairs with matching physical geometry and 12 mismatches. The
details are retained in `research/nasalseg-v2-audit.json`; mismatches are not
silently corrected.

This confirms data integrity only. NasalSeg does not provide tumor,
neurovascular, approach, or surgical-access reference labels.

The subsequent work goes beyond that audit: all 118 eligible scans were
evaluated in the independent boundary-sphere computational benchmark and the
actual production-engine airspace benchmark. See `research/AIRSPACE_BENCHMARK_RESULTS.md`
and `research/results/production-public-v3/summary.json`. The production run
records per-model configurations and hashed upstream target/anchor records.
Its verifier checks 130-case accounting, 12,310 feasible trajectory records,
set identities, pair constraints and recomputed statistics. This is execution
and internal-consistency evidence, not operative reference agreement.

Independent phantom evidence in `research/numerical-benchmark` records zero
false-feasible classifications in 99 sheared-cell queries, but 41 conservative
false blockages. Angular error at the finest tested grid remains 11.368% for one
small off-axis target. The numerical gate is not passed by averaging that away.
The historical angular failures remain unchanged. A subsequent geometry-aware
production method passed all 82 unobstructed analytic comparisons with maximum
relative error 0.0001204123% (prespecified threshold 1%), including the previously
missed narrow cap. Independent replay is in
`research/geometric-angular-independent-replay`. Protected-structure quadrature
is outside that method's scope and its general accuracy gate remains open.

Optional voxel-cell refinement now has independent box-optimization evidence:
530 synthetic and 120 public-case queries, zero observed false-clear or
clearance-bound violations, with identical record hashes on replay. See
`research/voxel-refinement-v5` and `research/voxel-refinement-independent-replay`.
These data do not establish safety against missing or inaccurate anatomy.
The frozen query protocol was subsequently applied to all 118 eligible cases:
4,720 queries with zero observed false-clear/bound/policy violations and 1,190
conservative budget fallbacks. See `research/voxel-refinement-full-cohort`.
This is bounded per-case sampling, not exhaustive trajectory validation.

Native macOS Qt/VTK smoke evidence is in `research/desktop-evidence` and
`research/native-bundle-evidence`; the frozen app passed eight runtime checks
outside the checkout. Offscreen tests do not substitute for those native runs,
and neither substitutes for human acceptance.

Consult `docs/RELEASE_GATES.md` for the unfinished anatomical, numerical, human
usability and native-packaging gates.

## Not established

- Accuracy of automatic skull-base anatomy or tumor segmentation.
- Completeness of patient-specific carotid, optic, cranial-nerve, or
  perforator anatomy.
- Endoscopic visibility, tissue mobility, hemostasis, dissection planes,
  reconstruction constraints, or staged-debulking behavior.
- Agreement with cadaveric, tracked-instrument, operative-video, or clinical
  outcomes.
- Safe resectability, complication risk, or superiority of an approach.

The software must not be used to select or conduct patient treatment.
