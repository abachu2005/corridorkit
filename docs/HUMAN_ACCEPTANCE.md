# Independent review required before anatomical release

Status: **not executed**. Automated tests cannot fill out this record.

Use the frozen build and a reviewer-approved, de-identified retrospective case
under the institution's applicable permissions. The public NasalSeg airspace
benchmark is not a substitute for a tumor/corridor reference.

## Record before review

- Reviewer name, role, date, software version and executable SHA-256.
- Case identifier and hashes of CT, masks and any registered MRI.
- The reference definition for each opening, protected structure and target.
- Whether any anatomy is unavailable, ambiguous or outside the field of view.
- A priori physical error tolerances agreed with the PI and the reason for them.
- Independent reference measurements and how they were obtained.

## Anatomical checks

1. Confirm RAS orientation, units, image geometry and left/right labels.
2. Inspect target and protected labels in all three planes and in 3D.
3. Explicitly identify missing vascular/neural structures; verify that unknown
   anatomy suppresses measurements rather than being treated as free space.
4. Review EEA and transmaxillary portal positions, normals and aperture sizes.
5. Review every approach-specific virtual bone-removal mask and its boundaries.
6. Confirm shaft radius, length and tip working radius against the intended
   instrument model; document unmodeled handles, optics and tissue effects.
7. Review feasible witnesses and near-boundary classifications against the
   independent reference, recording disagreements rather than editing them away.
8. Keep geometric target coverage distinct from resectability or surgical safety.

## Desktop tasks to execute without developer assistance

1. Import CT and a supported mask; verify physical alignment.
2. If applicable, propose MRI registration, inspect landmarks and overlays,
   approve/reject it and verify the saved transform direction.
3. Edit a portal and target, undo/redo, and confirm displayed geometry follows.
4. Paint a correction, undo/redo, apply it, and verify review approval invalidates.
5. Run EEA, transmaxillary and combined analyses; distinguish union coverage from
   simultaneous two-instrument feasibility.
6. Cancel a calculation and confirm no stale result can be exported.
7. Save/reopen the session and compare geometry, image layers and review state.
8. Replace an input mask externally and confirm previous approval is invalid.
9. Export results, inspect units and limitations, and trace witnesses back to CT.
10. Record confusing controls, errors, task duration and any assistance needed.

## Outcome

For each task record pass/fail/not-applicable, evidence, reviewer comment and
issue reference. The final decision must distinguish software usability,
anatomical reference agreement and clinical validation. A successful desktop
session alone does not establish the latter two. Failed tasks require fixes and
a new independent review; do not retroactively relabel the original record.
