# Reproducing the v0.2.1 software results

This protocol reproduces software behavior, not clinical validity. No private
patient data are required. Public CTs and model weights are downloaded from
their original providers and are not redistributed by this repository.

## Minimal data-free example

Use Python 3.11 or newer:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
skullbase-corridor synthetic example.case.json
skullbase-corridor analyze example.case.json example.result.json
skullbase-corridor doctor
```

The synthetic command is deterministic and needs no image data, model weights,
GPU, cloud account, or credentials.

## Verified source and package build

```bash
python -m pip install -e '.[desktop,test]' build
python packaging/verify_source_release.py --desktop --tests --build \
  --output dist/source-release-v0.2.1 \
  --report dist/source-release-v0.2.1-report.json
python packaging/build_slicer_package.py
python packaging/verify_slicer_package.py \
  dist/SkullBaseCorridor-Slicer-5.12-0.2.1.zip
```

GUI tests are intentionally isolated into separate processes by the source
verifier because a combined Qt test process can hang.

## Local model-assisted Slicer workflow

1. Install 3D Slicer 5.12.
2. Extract `SkullBaseCorridor-Slicer-5.12-0.2.1.zip`.
3. Add the extracted
   `SkullBaseCorridor/slicer/SkullBaseComparison` directory under Slicer's
   **Edit → Application Settings → Modules → Additional module paths**.
4. Restart Slicer and open **Skull-base approach comparison** under **IGT**.
5. Load a CT, place the Target point, and click **Plan Corridors**.

The first setup provisions an isolated Python 3.11+ runtime and installs
`TotalSegmentator==2.11.0`; first model use downloads upstream weights. These
steps require network access and several gigabytes of disk. CT inference and
analysis run locally by default, and later runs can reuse local model and
content-hash caches. The optional Azure backend is not required.

## Public multi-case evaluation

The evaluated cohort is NasalSeg v2, Zenodo record
[13893419](https://zenodo.org/records/13893419), declared CC-BY-4.0. The archive
SHA-256 used here is
`60c6facf843685802c39e4adff4a05c081c1c4b6175c9cb573745c55abb0fa6a`.

```bash
python research/tools/download_nasalseg.py
```

P001–P003 were used for the integrated acceptance. Manual labels selected a
deterministic evaluation target but were not supplied to anatomy inference or
path evaluation. The compact, shareable result ledger is
`docs/evidence/multicase-integrated-acceptance-20261008.json`. It contains
checksums, targets, timings, candidate counts, typed states, and interpretation
limits. It does not contain CTs, labels, credentials, or identifying metadata.

## Expected limitations

- Model predictions require anatomical review.
- CT and the selected models do not represent complete cranial-nerve, dural,
  cavernous-sinus, ophthalmic-artery, or other critical anatomy.
- `model_feasible` is feasibility under represented geometry, not safety.
- Sampled failure is not proof that no operative path exists.
- Local inference time depends on CPU/GPU, memory, model cache, and image size.
- The optional Azure timing ledger is not a local performance guarantee.
