# Native Slicer verification — 2026-10-07

Slicer 5.12.4 (Intel build under Rosetta), macOS 15.4. External engine:
`/opt/anaconda3/bin/python3`. This is local software verification, not clinical
validation, independent usability acceptance, or cross-platform certification.

## Release-bound rerun

On 2026-10-08, the integration protocol was rerun from a detached checkout of
tag `v0.2.1` at commit
`a0c6933258f60e370747cfd4096b9445eff8a572`. It passed in the official signed
and notarized Slicer 5.12.3 macOS amd64 distribution under Rosetta 2 on macOS
15.4. The machine-readable provenance and results are retained in
`docs/evidence/native-slicer-v0.2.1-20261008.json`.

The initial 5.12.4 application used for attempted reruns was damaged
(`codesign --verify --deep --strict` reported a missing or invalid sealed
resource). The clean 5.12.3 distribution passed signature and Gatekeeper
verification. Its first launch required several minutes for Rosetta translation
of Slicer's module graph; this was not a project-code failure.

The official installer SHA-512 matched upstream:
`122c5c33d68189cd5b8212ad489c7fa319ae2ba8126014bfb7c4ed1a4a02475fe1b78d45d71b22685be255e22cdb1ae7e696d05ee34b031795187f0a1c38a6e1`.

## Passed native checks

- Landmark move, deletion, extra/undefined and out-of-volume invalidation.
- Full-grid KJI → XYZ export with rotated anisotropic RAS geometry.
- Five coverage categories with exact segment voxel and physical coordinates.
- Actual external numerical engine execution on a two-voxel synthetic target.
- Actual asynchronous dialog export → process → log → coverage display.
- Scene save/reopen retaining CT, landmark and shaft references.
- UW original CT dimensions 367 × 449 × 304 and all 14 atlas components.
- Native atlas screenshot: `research/slicer-verification/atlas-native.png`.
- Target-first proposal flow on a synthetic full-grid CT: bilateral anatomy
  export, automatic candidate generation, exact entry placement, separate
  portal/shaft model creation, conditional state when protected anatomy is
  absent, and stale-result invalidation after moving the target.

Machine-readable results are in `research/slicer-verification/integration.json`
and `atlas-native.json`. The synthetic coverage fixture reported both target
voxels reached by both approaches; this says nothing about operative anatomy.
Slicer emitted subject-hierarchy warnings during synthetic scene teardown;
the asserted scene-reference and segmentation tests passed.

## Corrected by native testing

Missing markup removal/position-state observers left stale results after point
deletion. The module now observes these events explicitly. PythonQt requires
the `noneEnabled` property for segment selectors and `.data()` for reading
process `QByteArray` output; both incompatibilities were corrected.
The target-proposal dialog also avoids PythonQt's built-in `QDialog.result`
slot by storing its JSON response as `proposalResult`.

## Reproduce

Run from the repository root, with Slicer installed and engine dependencies:

```sh
SKULLBASE_PYTHON=/opt/anaconda3/bin/python3 \
 ~/Applications/Slicer-5.12.4.app/Contents/MacOS/Slicer \
 --no-splash --python-script "$PWD/slicer/verify_in_slicer.py"
~/Applications/Slicer-5.12.4.app/Contents/MacOS/Slicer \
 --additional-module-paths "$PWD/slicer/SkullBaseComparison" \
 --no-splash --python-script "$PWD/slicer/verify_atlas_in_slicer.py"
SKULLBASE_PYTHON="$PWD/.venv/bin/python" \
 arch -x86_64 ~/Applications/Slicer-5.12.4.app/Contents/MacOS/Slicer \
 --disable-settings --disable-cli-modules --no-splash --no-main-window \
 --python-script "$PWD/slicer/verify_target_proposals_in_slicer.py"
```

The atlas test requires the separately licensed public-data cache. It creates
no fabricated entry, surgical target, or operative route. The atlas's incomplete
ICA/nerve coverage cannot establish clearance. Frozen desktop installers,
notarization, clean-machine dependency installation and other platforms remain
outside this verified source/Slicer distribution.
