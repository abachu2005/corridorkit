# Scan-centered approach comparison

This workflow displays modeled access, not safe resection or a surgical
recommendation. A transmaxillary increment does not establish that Caldwell–Luc
is necessary. The exact operative variant and anatomy require expert review.

## Inspect a case

For surgeon-supervised manual planning, choose the approach in the right-hand
Plan inspector and press **Draw entry → tip**. Click the entry and then the intended tip,
scrolling slices between clicks if needed. Instrument geometry uses blue for EEA
and orange for CTM. The inspector and path view identify unassessed intended paths.
Text is kept outside the scan; rings mark apertures and contours outline the target.
Drawing does not move the configured
portal or change the target: those are separate user-reviewed inputs.
**Check against anatomy** tests the exact segment against the configured aperture,
instrument length and supplied protected spheres/voxel masks. Unknown anatomy
is retained as unassessed, even when other structures have measurable clearance.
No virtual bone removal is applied by this conservative exact-path check.
The comparison analysis remains separate from this manually drawn path.

**Save** persists intended paths with the review document. **Export plan** works
without a successful analysis and includes the input manifest and unassessed-path
warning. **Export analysis** exports computed approach coverage separately.
No user-drawn path is converted into an engine witness merely because it was drawn.

1. Open a saved corridor case, or import CT and configure/review target,
   protected structures and approach openings. Confirm RAS orientation and mm.
2. Run geometric analysis. A large axial CT is the primary workspace. The
   Plan inspector reports EEA, transmaxillary, combined and incremental sampled
   coverage. **Anatomy & configuration** opens detailed setup on demand.
3. Click a displayed target sample (within 3 mm) to filter to unconditional
   feasible paths reaching it. “Clear target filter” removes the filter. The axial
   overview shows the selected path plus one representative witness for each
   other visible approach; these are alternatives, not a simultaneous pair.
   Select an individual entry-to-target path under Computed paths. Path geometry
   follows the calculated entry point, direction and insertion depth—not a line
   invented between a portal and the target centroid.
4. Center the scan on the selected path and inspect axial, coronal and sagittal
   views. Axial framing retains the whole scan; support views fit the local path.
   A dashed projection is not necessarily in the displayed slice.
   Solid slice-intersection segments indicate where the path enters that slab.
5. Use the smaller support panel for coronal, sagittal or path-aligned CT,
   with 3D available on demand. Inspect the path-aligned CT plane to see the route.
   Off-plane anatomy is not represented as an obstacle-free guarantee.
6. Compare EEA-only, transmaxillary-only, shared and not-reached target samples.
   “Not reached” means not reached by evaluated paths, not proven inaccessible.
   Unavailable anatomy/results are distinguished from negative findings.
7. Review the separate simultaneous-instrument finding. Union coverage from
   alternative paths is not proof that those paths can be used together.

## Counts versus volume

Sparse target points are sample counts, not tumor-volume percentages. Physical
volume requires a complete voxel-center target representation with valid cell
weights. Neither point nor volume coverage implies safe resection.
For masks exceeding 2,000 voxels, import asks you to choose full voxel analysis
or a sparse preview. Full-volume target-directed computation can take much
longer; cancellation is available. The export includes a coverage subdirectory
with category counts and one CSV row per input target point.

## Invalidation

Changes to target, portal, instrument, mask or configuration invalidate previous
measurements. Rerun after changes. Export checks external input fingerprints;
files replaced on disk cannot silently retain approval.

## Local public example

For the fully segmented demonstration, use **Planning phantom** in the toolbar,
or double-click `packaging/Open Segmented Planning Demo.command`. It generates
a small, deterministic CT-like volume, a complete voxel target and invented
protected spheres. All geometry is explicitly synthetic, not patient anatomy.
The shell and air spaces are display context, not validated collision anatomy.

Entry disks are drawn with their correct projected orientation and diameter.
Full target masks have category-filled slice contours and cropped 3D label
surfaces; sparse points never get a fabricated enclosing tumor surface.
The selected shaft has a finite-capsule slice intersection plus a dashed
projection. The path-aligned plane shows physical shaft diameter and insertion
depth; a nonzero configured tip working radius is shown separately.
The instrument model is a rigid capsule, not a manufacturer-specific tool CAD model.

Use EEA / transmaxillary / combined display modes to reduce overlapping
annotations. Optional **Sampled shaft occupancy** shows the union of evaluated
feasible capsules intersecting the slice. It is not a smooth safe-access cone:
no convex hull is used, and gaps between sampled paths remain gaps.
The occupancy image is rasterized and is not an additional collision test.

Double-click `packaging/Open Planning Demo.command` or run it from Terminal.
It opens the existing public computational case and starts analysis. It uses
the local scientific Python environment; `CORRIDORKIT_PYTHON` can override the
interpreter. It is not the frozen native application.

The public example contains a real CT but computational target points and
openings, not tumor labels or surgeon-approved EEA/transmaxillary corridors.
The demo cannot establish clinical approach necessity.
