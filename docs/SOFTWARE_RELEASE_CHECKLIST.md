# CorridorKit software-publication and source-release checklist

Current source target: CorridorKit v0.3.0,
<https://github.com/abachu2005/corridorkit>. Historical reports do not establish
verification of this renamed release or tag.

Scope: surgeon-supervised geometric research software, synthetic fixtures and
existing public deidentified data. No private patient data, outcomes or clinical
efficacy study is required for this bounded software article. Passing this
checklist does not authorize treatment use.

Use checked boxes only for directly established items. This is not a declaration
that a release or manuscript is finished. `docs/RELEASE_GATES.md` retains the
broader research/translation history; its clinical gates have not been waived.

## A. Scope and manuscript

- [x] Define finite-instrument geometry, supervised input review, abstention
  and model-only interpretation in README and `SOFTWARE_PAPER.md`.
- [x] Distinguish sampled reach, simultaneous access, physical volume and
  protected-geometry angular limitations.
- [x] Provide an evidence ledger with frozen paths and negative numerical results.
- [ ] Responsible authors verify manuscript statements, authorship, contributions,
  related work, affiliations, conflicts, funding and venue requirements.
- [ ] Obtain/document the applicable institutional determination. Public
  deidentified data may still require a non-human-subjects determination or
  other institutional review; no approval/exemption is inferred or claimed.
- [ ] Recheck upstream public-data licenses, attribution and per-file terms.
  NasalSeg v2's retained manifest declares CC-BY-4.0; verify before redistribution.
- [ ] Review any shared paths, case IDs, review identities, images and screenshots
  for privacy and licensing. Prefer synthetic figures. No reidentification.
- [ ] Archive approved evidence and a fixed source version with durable
  identifiers. Do not invent a repository URL, release tag, DOI or submission.

## B. Current-source engineering gates

- [x] Provide a source verifier with required-module imports, source/package
  version agreement, installed entry-point metadata and CLI help checks.
- [x] Provide fresh-process test orchestration: numerical modules grouped,
  desktop/Qt-related modules individually isolated, including mixed modules.
- [x] Provide explicit failure/timeout reporting and regression tests for the verifier.
- [ ] Retain a successful current-source report with exact environment and
  passed/failed/skipped counts. Investigate failures; skips are not coverage.
- [ ] Retain a deterministic synthetic CLI generation → analysis smoke record
  from the installed wheel outside the checkout.
- [ ] Retain current native desktop evidence for claimed rendering features.
  Offscreen tests do not establish native VTK rendering or independent usability.
- [ ] Verify the chosen release on supported platforms or narrow declared support
  to those actually tested. Local CI definitions are not completed hosted CI runs.

Commands (Python >=3.11 with scientific and desktop dependencies):

```bash
python -m pip install -e '.[desktop,test]' build
python packaging/verify_source_release.py --desktop --tests \
  --report dist/source-check-new.json
```

`--tests` runs every `tests/test_*.py` discovered recursively. GUI classification
is conservative static detection of desktop/Qt references; newly introduced
indirect GUI helpers must be reviewed so they do not slip into the numerical
group. `conftest.py` imports Qt but does not create a QApplication. Each group
has a timeout (default 300 seconds, configurable with `--timeout`). Offscreen
Qt is forced and third-party pytest plugin autoload disabled. Failures do not
prevent the remaining groups from reporting. This works around a known
combined Qt-suite hang; it does not claim the lifecycle cause is fixed.

## C. Lightweight distribution gates

- [x] Source-only staging allowlist excludes public images, data caches,
  local sessions, screenshots, benchmark records and frozen app binaries.
- [x] Archive verification compares wheel Python files and sdist inputs
  byte-for-byte against the checkout, checks wheel version and reports SHA-256.
- [ ] Build current wheel and sdist with an unchanged source manifest during
  the build, inspect both and retain their hashes.
- [ ] Install and smoke the wheel outside the source tree; explicitly disclose
  shared system dependencies if using `packaging/verify_wheel.py`.
- [ ] Verify a genuinely clean dependency installation separately before
  claiming clean-machine reproducibility.
- [ ] Verify that the sdist can rebuild a matching package in the intended
  release environment, and review exact third-party notices.
- [ ] Review and approve any separately archived benchmark supplement:
  evidence paths in the manuscript refer to the development checkout, not
  evidence bundled in the lightweight source distribution.

```bash
python packaging/verify_source_release.py --desktop --tests --build \
  --output dist/source-release-new --report dist/source-release-new-report.json
python packaging/verify_wheel.py \
  dist/source-release-new/corridorkit-0.3.0-py3-none-any.whl
```

Choose fresh output/report paths; existing evidence is not overwritten.
The source build requires at least 512 MiB free as a minimum guard, not a
guarantee of enough space on every platform. It uses temporary staging and an
isolated PEP 517 backend, which may require network access for build tools.
The build does not copy public CT data or download it. It includes Python
source, tests, text docs, packaging scripts and the independent voxel-oracle
runner needed by tests. It omits native binary assets and historical
benchmark outputs; this is not a complete native-app build kit or the full
paper's reproducibility supplement.

The default `python` command may select an incompatible interpreter. On the
inspected workstation `/opt/anaconda3/bin/python3` is Python 3.11.7 with the
scientific/desktop dependencies and `build`; the default `python` is 2.7 and
Homebrew `python3` is 3.14 without those dependencies. Use the explicit
interpreter for local verification. Initial free disk was approximately
1.7 GiB on 2026-10-05, below the frozen macOS build's required 5 GiB.
Do not delete data caches, install a second large desktop stack, run an
expensive frozen build or publish artifacts as part of this source-only task.

## D. Separate clinical/translation gates — not completed here

- [ ] Independent expert review of anatomy, portal definitions and intended
  surgical-use assumptions.
- [ ] Independent clinician usability/acceptance study with an appropriate protocol.
- [ ] Application-specific physical tolerances and independent anatomical,
  cadaveric or tracked-instrument reference measurements where warranted.
- [ ] Additional study design, institutional approvals and regulatory review
  before introducing private records, patient studies or treatment use.
- [ ] Evidence for any future safety, efficacy, superiority or outcome claim.

These are not prerequisites for merely reporting bounded software methods,
but are prerequisites to the corresponding stronger claims. A software paper
must disclose their absence rather than relabel computational tests as clinical
validation.

## E. Separate native-distribution gates — not completed by source verification

- [ ] Current-source frozen app with matching source manifest and native smoke.
- [ ] Platform-specific installer checks and clean-machine testing.
- [ ] Exact binary dependency licenses, signing/notarization and distribution review.

Historical local macOS bundle/DMG evidence predates later planning changes.
It must not be advertised as a current-feature release. No public publishing
or expensive frozen-bundle build is performed by the source verifier.
