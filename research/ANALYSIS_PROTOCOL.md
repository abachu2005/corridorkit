# Frozen engineering analysis protocol

## Claims

The software measures access under a stated static geometric model. Primary
engineering endpoints are:

1. false-feasible collision classifications on analytical fixtures;
2. absolute distance and angle error against independent reference calculations;
3. target coverage convergence across the fixed sampling grid;
4. within-subject paired differences in modeled access between configurations.

No endpoint estimates safe resection, complication risk, or the optimal
operation.

## Development and evaluation separation

Subjects are assigned deterministically from a salted SHA-256 identifier to
development (60%), validation (20%), or evaluation (20%). All sides, targets,
perturbations, and repeated measurements from a subject remain together.

Preprocessing, mesh, sampling, and optimization parameters may be changed using
development cases. Validation cases select among complete prespecified
configurations. Evaluation cases are run once after configuration freeze.
Failures, exclusions, and abstentions remain in the report.

## Numerical studies

The fixed convergence grid is:

- 5 polar × 24 azimuth samples;
- 9 polar × 48 azimuth samples;
- 17 polar × 96 azimuth samples.

Successive coverage and solid-angle changes are reported by approach. A stable
sequence is evidence of numerical stability at those resolutions; it is not a
proof that no unsampled trajectory exists.

Portal-position sensitivity uses an explicitly assumed independent Gaussian
perturbation in each physical axis, with sigma reported in millimetres and a
fixed random seed. Quantiles from this experiment are sensitivity intervals,
not clinical confidence intervals or safety probabilities.

Sensitivity now optionally includes a shared relative target translation per
repeat (registration-like, not measured registration error) and outward sphere
boundary inflation (segmentation-like). Unsupported boundary geometry abstains
instead of silently ignoring the scenario. Unknown anatomy is never overridden
by a permissive approach setting in sensitivity or convergence. Sensitivity
counts abstention as zero **demonstrated** coverage and separately reports
abstention counts; this is not an assertion of zero true coverage. Convergence
reports unavailable measurements as null rather than a fictitious stable zero.
Repeat counts and perturbation scales must be valid and finite.

## Cohort statistics

Approaches are compared within subjects. Report paired differences, their full
distribution, mean, median, sample standard deviation, range, and subject-level
bootstrap intervals. Target voxels are never counted as independent subjects.
Any confirmatory comparison and multiplicity strategy must be declared before
the held-out evaluation.

Paired summaries reject unmatched subjects, nonfinite/missing values, invalid
bootstrap repeat counts, and overflow by default. Explicit exclusion mode lists
every excluded ID. For repeated measurements, require exactly matched repeat
keys within each subject, average within-subject differences first, and
bootstrap equally weighted subjects. Repeated targets, sides, and scenarios
never increase the effective subject sample size.

The public v2 analysis uses the separate frozen
[airspace computational protocol](AIRSPACE_BENCHMARK_PROTOCOL.md); it does not
claim anatomical surgical comparison. A second unchanged execution checks
deterministic reproduction only and is not a second held-out optimization trial.

## Registration

MRI-to-CT rigid registration uses Mattes mutual information and saves the
transform plus optimizer diagnostics. Every transform remains `review required`
until overlay and landmark review are recorded. Registration proposals are not
silently accepted.
