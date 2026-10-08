# UW skull-base atlas: acquired and inspected

Inspected 2026-10-05. This is real, published anterior skull-base anatomy, useful
for examining relevant structures before defining an EEA versus CTM comparison.
It is **not** evidence that either surgical route is feasible, safer, or superior,
and it does not provide a petroclival target or complete route annotations.

## Source, licensing, and bounded acquisition

Source: Konuthula, Perez, Maga, Abuzeid, Moe, Hannaford, and Bly,
[Automated atlas-based segmentation for skull base surgical planning](https://digital.lib.washington.edu/researchworks/items/2b0f0e1e-ae81-4bf8-9b79-8dc6872b5ecc/full),
issued 2020-09-09; persistent identifier
[hdl:1773/46259](https://hdl.handle.net/1773/46259).
The source describes a synthetic averaged CT constructed from **six subject
scans**, not an individual clinical CT or a full clinical cohort download.

The data license is
[CC BY-NC-SA 3.0 United States](https://creativecommons.org/licenses/by-nc-sa/3.0/us/).
Keep author/source attribution and this license with the originals and derived
inspection image; noncommercial and share-alike restrictions are separate from
the application's code license. Do not bundle this dataset as unrestricted
commercial application content. The repository's deposit `license.txt` is not a
substitute for the public data license recorded in `dc.rights` / `dc.rights.uri`.

The public HTML disclosed the item API:
`https://digital.lib.washington.edu/server/api/core/items/2b0f0e1e-ae81-4bf8-9b79-8dc6872b5ecc`.
The downloader follows its returned bundles link, selects the `ORIGINAL`
bundle, follows its returned bitstreams link, then each selected bitstream's
actual `_links.content.href`. No byte URLs were inferred from filenames.

Downloaded unchanged originals, all under `data-cache/skullbase-atlas/`:

- `CT_template_pub - Neeraja Konuthula.nrrd`: **77,669,909 bytes**;
  bitstream `59e583ef-faaa-47c7-975d-d63f187e57ad`;
  repository MD5 `e4d8f8f988ed59fc9d8ad99becf70651`.
- `Segmentation_atlas_pub.seg.nrrd`: **299,575 bytes**;
  bitstream `3c5ef237-048e-47b1-aa54-f55e4151c6ab`;
  repository MD5 `5f26dbd8b24dcacd19ed8209f8ba7fec`.
- `instructions_readme.txt`: **462 bytes**;
  bitstream `589d2caa-278d-4ff1-a3bb-37582763cdb5`;
  repository MD5 `b8563e4d55aeb56fd0110e4472cb0d0e`.

Total originals: **77,969,946 bytes (77.97 decimal MB)**. The alternative
`CT_template_pub.nii` (200,376,480 bytes) was explicitly excluded. No full
subject datasets were acquired. `manifest.json` records source metadata,
returned download URLs, byte counts, MD5 verification, SHA-256 hashes, headers,
physical geometry, original instructions, component voxel counts, and image
generation details.

Repeat from the repository root:

```sh
/opt/anaconda3/bin/python research/prepare_skullbase_atlas.py --contact-sheet
/opt/anaconda3/bin/python -m pytest tests/test_skullbase_atlas.py -q
```

The script needs `requests` and `numpy`; `--inspect-payload` also needs
SimpleITK, and `--contact-sheet` additionally needs matplotlib. These are
already available in the inspected Anaconda environment. It refuses a selected
set over 99,000,000 bytes, streams downloads with size/checksum checks, reserves
512 MB disk headroom, rejects unexpected hosts/redirects, and reuses originals
only after verifying them. A mismatched existing original is not overwritten.
Temporary download files are removed on ordinary failure. No decompressed
CT/segmentation copy is written to disk.

## Exact geometry and original segmentation representation

CT: float32, gzip NRRD, **367 × 449 × 304** voxels, **0.46875 mm**
isotropic spacing. Native header coordinates are LPS, with a small in-plane
rotation; direction vectors and origin are retained in the manifest.
Voxel-center axis-aligned physical bounds in LPS millimeters:

- L: −107.8933 to 78.9318
- P: −111.4914 to 110.7810
- S: 45.0285 to 187.0597

Segmentation: unsigned-byte, gzip NRRD, **14 × 282 × 230 × 85** in NRRD
axis order. The first axis is a nonspatial `list` axis. Spatial spacing is
**0.46875 × 0.46875 × 1.25 mm**. Its native coordinates are RAS, with positive
axis-aligned spatial directions. Voxel-center physical bounds in RAS mm:

- R: −48.1096 to 83.6092
- A: −14.3350 to 93.0088
- S: 51.2902 to 156.2902

The header does not explicitly declare `space units`; millimeters follow the
medical-image convention and Slicer reference geometry. The manifest also
records voxel-edge bounds, which differ from center bounds by half a voxel.
These are **image field bounds**, not proof that all structures inside are
segmented or that a route is usable.

This is a legacy Slicer segmentation with **one independent binary mask per
component**, not a modern single labelmap with labels 1–14. It has no explicit
`SegmentN_Layer` or `SegmentN_LabelValue` fields. SimpleITK reads it as a
282 × 230 × 85 vector image with 14 components; NumPy order is
85 × 230 × 282 × 14. Payload values are only 0 and 1. All 14 masks are nonempty,
and all measured nonzero extents match their header extents.

**31 voxels belong to multiple components.** Flattening by `argmax`, assigning
one integer label per voxel, or saving a combined labelmap would lose original
information. The downloaded `.seg.nrrd` has been preserved byte-for-byte.
Header parsing additionally preserves modern layer/label metadata when present,
but the optional payload inspector intentionally expects this atlas's legacy
component format.

Do not align CT and segmentation by array index: their origins, dimensions,
spacings, direction matrices, and native coordinate conventions differ.
`Segmentation_ReferenceImageExtentOffset` is `65 188 2`; it is already reflected
in the stored cropped-image origin and must not be added again.

## Available annotations: exact published names

1. eyes
2. septum
3. optic nerves
4. internal carotid artery
5. nasolacrimal duct
6. foramen ovale
7. foramen spinosum
8. foramen rotundum
9. vidian canal
10. pterygopalatine fossa
11. pituitary
12. cavernous sinus
13. extraocular muscles
14. bone of anterior cranial fossa

Names are preserved verbatim. Bilateral structures are often combined in one
mask; do not infer left/right segment identities or specific carotid portions
from these names.

Important limited annotation: the internal carotid artery mask has just
**720 voxels**, with RAS center bounds R 2.0467–25.9529, A 16.1338–24.5713,
S 111.2902–118.7902 mm. Its craniocaudal extent is only **7.5 mm** between
voxel centers. This is not a full ICA tree and cannot establish
petrous/paraclival vessel avoidance. The source abstract also reports that
carotid artery, foramen spinosum, and foramen rotundum required more than minor
corrections in its clinical-usability assessment.

## What this supports for EEA versus CTM

Supported: a genuine averaged CT for inspecting sinonasal/anterior skull-base
bony context and the relationship of the published septum, pituitary, optic,
parasellar, vidian, and pterygopalatine annotations. It is a substantially more
relevant anatomical starting point than an arbitrary air-volume corridor.
The underlying CT can be inspected for candidate anatomical landmarks, but
visible anatomy must not be conflated with validated segmentation.

Missing **explicit annotations** include a petroclival lesion/target; clivus
and petrous apex as separate masks; complete petrous/paraclival ICA;
brainstem and basilar artery; cranial nerves III–XII; a separately segmented
maxillary sinus, maxillary walls/entry window, nasal airway or nostril entry;
EEA/CTM surgical openings, drilling/resection plans, trajectories, instrument
envelopes, and route-specific exposure. Some of these structures may be visible
in CT or included in a broad bone mask, but that does not create the absent
semantic annotations.

Before quantitative EEA/CTM comparison, an anatomically qualified reviewer must
identify a relevant target and entry windows in physical coordinates, confirm
CT field coverage and image/mask alignment, annotate required missing
obstacles/structures, and define allowed surgical modifications consistently.
Do not interpret missing labels as free space or use the pituitary display
crosshair as a proposed petroclival surgical target. No route, reachability,
clearance, or surgical recommendation was generated here.

## Inspection image and Slicer use

`data-cache/skullbase-atlas/atlas-contact-sheet.png` shows axial, coronal, and
sagittal views, first CT only and then each original component independently
contoured. CT is linearly resampled **in memory** into the segmentation's
physical grid using SimpleITK's common LPS convention. The crosshair is the
center of the published pituitary mask extent, index (132, 66, 51), solely to
show useful anatomy. The display range is −500 to 1500; no new tissue
classification or anatomical route is introduced. The resampled CT intensity
range is approximately −1003.21 to 2618.49.

The original README instructs users to install Slicer, drag in the CT, then
drag in the segmentation to show a color overlay. It references the historical
Slicer revision r28738 and abbreviates filenames; the actual preserved
filenames above should be used.

In a current 3D Slicer installation:

1. Load `CT_template_pub - Neeraja Konuthula.nrrd` as a volume.
2. Load `Segmentation_atlas_pub.seg.nrrd` **as a segmentation**, not a scalar
   labelmap; verify the 14 named segments.
3. Inspect overlay alignment in all three planes; use Segmentations / Segment
   Editor to show individual structures and optionally create a surface.
4. Keep the original files unchanged. Save any edited scene or derived masks
   separately with attribution and a description of modifications.

This work verified the file hashes, headers, independent binary masks, and
physical-coordinate resampling using SimpleITK. It did **not** run a Slicer GUI
session or obtain specialist anatomical validation; those remain necessary
before trusting alignment for measurements.
