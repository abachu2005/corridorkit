# Model resource ledger

Registry schema: **1.0.0**  
Evidence review date: **2026-10-07**

The machine-readable authority is
`src/skullbase_corridor/anatomy/resources.py`. This ledger records potential
anatomy resources, not installed integrations. `downloadable` means an upstream
artifact was verified; it does **not** mean this application can run it. Every
entry is currently `research_only` or `unavailable`, and none is a production
default.

Roles:

- `proposal`: may generate a reviewable suggestion after a future, validated
  adapter is implemented.
- `reference`: annotated data or landmark source for research.
- `evaluation`: candidate external benchmark, subject to its usage terms.
- `correction`: may assist a human correction workflow.
- `unavailable`: cannot be used here because required artifacts or adequate
  reuse permission were not verified.

License status is deliberately separate from the license text.
`review_required` means terms exist but need legal/model-specific review;
`unknown` means no governing license was verified. Article licenses are not
treated as software/model licenses.

## Verified resources

### TotalSegmentator specialist tasks

- **Resources:** `head_glands_cavities`, `head_muscles`,
  `headneck_bones_vessels`, and `headneck_muscles`.
- **Role/status:** proposal; research only.
- **Modality:** CT.
- **Relevant labels:** optic nerves, eyes/lenses, nasal cavity and pharyngeal
  spaces, hard palate, auditory canals, masticatory muscles, styloid processes,
  internal carotid arteries/internal jugular veins, pharyngeal/prevertebral
  muscles, craniofacial bones/sinuses, jawbones and teeth (task-dependent).
- **Artifacts:** upstream task weights are downloadable by the
  TotalSegmentator CLI. They are not bundled, checksum-pinned, or connected to
  a local adapter.
- **License:** Apache-2.0 for these tasks per the upstream open-task list
  (verified). This statement does not cover separately listed licensed tasks.
- **Limits:** broad head-and-neck outputs are not skull-base corridor
  validation and require expert review.
- **Source:** <https://github.com/wasserth/TotalSegmentator#available-models>

### NasalSeg

- **Role/status:** reference and evaluation; research only.
- **Modality/labels:** CT; left/right nasal cavities, nasopharynx, and
  left/right maxillary sinuses.
- **Artifacts:** versioned annotated data and code are public; pretrained
  weights were not verified.
- **License:** dataset CC-BY-4.0 (verified). The dataset license does not
  establish a model license.
- **Limits:** five labels and no verified runnable model artifact.
- **Source:** <https://zenodo.org/records/13893419>

### Slicer Automated Dental Tools — AMASSS and ALI-CBCT

- **Role/status:** AMASSS proposal; ALI proposal/correction; research only.
- **Modality/labels:** CBCT; craniofacial bone/dental/airway masks for AMASSS
  and craniofacial landmarks for ALI.
- **Artifacts:** each Slicer module exposes a “Download latest models” action,
  but the resulting model versions/checksums are not pinned here.
- **License:** review required. The README says Apache-2.0, while the repository
  `LICENCE` contains Slicer terms; model-specific terms were not verified.
- **Limits:** Slicer-dependent workflow; ALI produces landmarks rather than
  dense critical-structure masks.
- **Source:** <https://github.com/DCBIA-OrthoLab/SlicerAutomatedDentalTools>

### ToothFairy3

- **Role/status:** reference and evaluation; research only.
- **Modality/labels:** CBCT; 77 classes including jaws, teeth/pulps, inferior
  alveolar canals, maxillary sinus, pharynx, incisive canals, and lingual canal.
- **Artifacts:** training data are available after sign-up; no official
  production weights were verified. The test set is private.
- **License:** CC-BY-NC-SA for the training dataset (verified).
- **Limits:** non-commercial/share-alike terms and CBCT domain.
- **Source:** <https://toothfairy3.grand-challenge.org/dataset/>

### CT-SCOPE

- **Role/status:** reference and evaluation; research only.
- **Modality/labels:** CT; osseous structures surrounding the paranasal sinuses.
- **Artifacts:** dataset, implementation and 2-D/3-D/final models are published.
  The final model trained on all released slices, so it is not independent
  evaluation evidence for that dataset.
- **License:** CC-BY-NC-ND (verified).
- **Limits:** 13 manually annotated subjects and no-derivatives terms.
- **Source:** <https://zenodo.org/records/15085103>

### HaN-Seg

- **Role/status:** reference and evaluation; research only.
- **Modality/labels:** paired CT and T1 MR; 30 organs at risk, including carotid
  arteries, optic nerves/chiasm, brainstem, spinal cord, mandible, oral cavity,
  and pituitary.
- **Artifacts:** version 1.0 provides 42 public training cases and reference
  masks; it is data, not production weights.
- **License:** CC-BY-NC-ND-4.0 (verified).
- **Limits:** CT and MR are not registered; non-commercial/no-derivatives terms.
- **Source:** <https://zenodo.org/records/7442914>

### SegRap2023

- **Role/status:** reference and evaluation; research only.
- **Modality/labels:** paired non-contrast/contrast CT; 45 OARs and two tumor
  volumes. Relevant OARs include optic nerves/chiasm, internal auditory canals,
  pituitary, temporal lobes, and tympanic cavities.
- **Artifacts:** training labels and third-party challenge inference code/weights
  are downloadable. The released inference path requires paired
  contrast/non-contrast CT.
- **License:** review required. Challenge terms prohibit redistribution; the
  paper’s CC license is not assigned to the dataset.
- **Limits:** nasopharyngeal-cancer/radiotherapy cohort and restricted reuse.
- **Source:** <https://segrap2023.grand-challenge.org/dataset/>

### TopCoW

- **Role/status:** reference and evaluation; research only.
- **Modality/labels:** CTA and MRA; Circle of Willis vessel segments, including
  bilateral ICAs, ACAs, MCAs, PCAs, communicating arteries, and basilar artery.
- **Artifacts:** paired annotations, organizer baseline weights, and several
  best-performing challenge Docker images are public. They are large, unpinned
  local dependencies and remain research-only.
- **License:** open use with source attribution; commercial use requires prior
  data-owner permission (verified).
- **Limits:** angiographic input and Circle-of-Willis scope do not directly
  cover routine skull-base CT.
- **Sources:** <https://topcow24.grand-challenge.org/data/> and
  <https://zenodo.org/records/15692630>

### University of Washington synthetic skull-base CT atlas

- **Role/status:** reference and evaluation; locally acquired and inspected.
- **Modality/labels:** averaged CT with 14 components including septum, optic
  nerves, ICA, foramina, vidian canal, pterygopalatine fossa, pituitary and
  cavernous sinus.
- **Artifacts:** original CT and segmentation are checksum-verified in
  `data-cache/skullbase-atlas/`; they remain outside source distributions.
- **License:** CC-BY-NC-SA-3.0-US.
- **Limits:** synthetic average of six scans; ICA annotation spans only 7.5 mm;
  no lesion target, maxillary window or route annotation.
- **Source:** <https://digital.lib.washington.edu/researchworks/items/2b0f0e1e-ae81-4bf8-9b79-8dc6872b5ecc/full>

### SlicerMorph ALPACA

- **Role/status:** proposal and correction; research only.
- **Modality/labels:** 3D surfaces; user-supplied surface landmarks.
- **Artifacts:** source implementation is public. ALPACA is point-cloud
  alignment/deformable registration and has no pretrained model weights.
- **License:** BSD-2-Clause for SlicerMorph code (verified).
- **Limits:** requires a source mesh and landmarks; does not produce voxel
  segmentations.
- **Source:** <https://github.com/SlicerMorph/SlicerMorph>

### CNTSeg

- **Role/status:** unavailable.
- **Modality/labels:** multimodal MR/diffusion inputs (T1w, FA, fODF peaks);
  CN II, III, V, and VII/VIII tracts.
- **Artifacts:** training code is public, but pretrained weights are not.
- **License:** unknown.
- **Limits:** inputs differ from CT workflow, weights are unavailable, and no
  local adapter exists.
- **Source:** <https://github.com/IPIS-XieLei/CNTSeg>

### OpticNerveSeg

- **Role/status:** reference and evaluation; research only.
- **Modality/labels:** multimodal MRI; optic nerves, chiasm, and tracts.
- **Artifacts:** derived masks/metadata are public; original HCP MRI must be
  obtained separately. No pretrained weights were verified.
- **License:** annotations/metadata CC-BY-4.0; HCP Open Access terms govern
  source MRI (verified).
- **Limits:** MRI-only and dependent on separately obtained HCP data.
- **Source:** <https://zenodo.org/records/21348649>

### SinusSegment

- **Role/status:** unavailable.
- **Modality/labels:** CT; maxillary, anterior/posterior ethmoid, sphenoid and
  frontal sinuses plus nasal cavity.
- **Artifacts:** the repository includes `default_parameters.pth`.
- **License:** unknown for repository code/weights. The associated article is
  CC-BY-NC, which does not license the repository artifact.
- **Limits:** no verified artifact reuse terms, pinned checksum, or local
  adapter. It must not be selected as a production default.
- **Source:** <https://github.com/rheadkaul/SinusSegment>

## Safety invariant

`validate_registry()` rejects duplicate or incomplete entries, non-HTTPS source
URLs, and any production default that is unavailable or not explicitly
`supported`. The current registry intentionally has no production defaults.
