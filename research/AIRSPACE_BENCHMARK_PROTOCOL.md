# Frozen public airspace computational benchmark, version 1

Frozen before the first geometry evaluation on NasalSeg v2. The executable
configuration and exact subject assignments are written to a hashed manifest
before any case is loaded. No tuning or selection uses benchmark outcomes.
The existing v2 integrity audit is prior knowledge, NOT corridor evaluation.

## Scope and non-claims

This is a **scan-based controlled airspace computational benchmark**, not an
anatomical surgical comparison. The five source labels supply verified interior
voxel centers and an airspace/non-airspace boundary. No carotids, tumors,
petroclival targets, surgical openings, or surgical routes are fabricated.
Entries are interior computational anchors, not external entry portals.
Targets are labeled airspace centers, not operative targets. Comparisons concern
finite computational capsule lengths/radii, not EEA versus transmaxillary access.

The small cropped v2 volumes are not full craniofacial scans. Cropping,
segmentation error, unknown intensity calibration, and physical image uncertainty
remain. Expert anatomical review is **incomplete** for every subject; surgical
validation and approach comparison are blocked.

## Frozen eligibility, sampling, geometry, and scenarios

- Match exact case IDs after documented image/seg suffix removal; duplicate IDs
  are a fatal data error, and unmatched subjects remain excluded records.
- Require finite 3D arrays, integral labels in 0–5 with nonzero foreground,
  nonsingular matching RAS affines (absolute tolerance 0.0001 mm, no relative
  tolerance), and orthogonal physical voxel axes. Do not repair mismatches.
- Report full affines, physical FOV bounds, voxel dimensions, intensity
  percentiles without assuming HU, clipping indicators, and source checksums.
- For each nonzero label require at least three voxel centers at least 2 mm
  inside that label, using padded Euclidean distance transform. Pick the deepest
  center and lexicographic first/last eligible centers. Deduplicate exact indices.
  Missing label candidates remain explicit label-level exclusions.
- The first and last eligible label's deepest centers are computational anchors
  (one anchor if only one label qualifies). All selected centers are targets.
  Targets and anchors are confirmed to be in their source label. Self-segments
  are excluded with a reason. No randomness chooses favorable trajectories.
- Obstacles are spheres centered on non-airspace voxels sharing a face with the
  union of labeled airspaces; radius is half the physical voxel diagonal. This
  is a conservative **boundary-sphere surrogate**, not a tissue segmentation or
  exact voxel-box model. Boundary labeling and thin structures remain uncertain.
- Test straight finite capsules joining an anchor to a target, radii
  `[0, 0.5, 1.0]` mm and maximum lengths `[10, 30, 60]` mm.
  A touching capsule is blocked. These are final static segment poses; insertion
  from outside the scan and operative accessibility are not evaluated.
- Accelerated collision clearance uses a KD-tree broad phase enclosing the
  segment's midpoint ball and the largest tested capsule/boundary radius.
  Independent reference checks every obstacle with a separately coded
  vectorized projection formula. Values outside the broad-phase search are
  censored at the common 2 mm clearance ceiling, explicitly reported as such.
  Agreement tests classifications across the entire frozen grid and capped
  clearance errors. This checks two implementations of the same sphere model,
  not independent anatomical ground truth.
- Scenarios: baseline; translate obstacle centers +1 mm or -1 mm along RAS X
  (registration-like relative displacement); inflate boundary sphere radii
  by 1 mm (segmentation-boundary erosion of free space). These are deterministic
  stress scenarios, NOT calibrated clinical error distributions. Preserve all
  scenarios, including abstentions.
- If a capsule (including uncertainty margin) is not contained in image FOV,
  abstain conservatively; never call unobserved space free. Count abstention as
  zero demonstrated reachability in the conservative coverage denominator, but
  retain a distinct status and abstention rate. Missing data are never omitted
  from case status accounting.

## Split, inference, reproducibility

SHA-256 subject assignment uses salt `nasalseg-airspace-v1-20261001`, fractions
60% development / 20% validation / 20% evaluation. The archive's P identifiers
are the available grouping unit; independent patient identity across source IDs
is not independently verified. All labels, anchors, targets, configurations,
and scenarios from an identifier stay in its partition. No configuration
selection or statistical hypothesis test is performed.

Within each subject, average demonstrated reachability across nonself
anchor-target pairs. The prespecified descriptive contrast is 1 mm versus
0 mm radius at 30 mm length in the baseline scenario. Bootstrap subjects with
equal weights (5,000 replicates, seed 20261001), never individual trajectories
or labels. Report partitions separately, exclusions and full paired differences.
No inferential claim about surgical anatomy or superiority is permitted.

The complete JSON contains every case status, label selection/exclusion,
trajectory coordinates, capped distances, scenario, grid classifications,
per-subject effects, and source/configuration/code hashes. Reproduction writes
a distinct output directory; frozen outputs are never silently overwritten.
Only one image/label pair is temporarily extracted at a time. The trusted source
archive is preserved in ignored `research/data-cache`; patient image files are
never added to git.
