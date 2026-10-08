# CorridorKit SoftwareX manuscript

- `softwarex-submission.pdf` is the Elsevier-formatted submission manuscript.
- `softwarex.pdf` is the illustrated review manuscript.
- `softwarex.md` is the editable manuscript source.
- `softwarex.tex` is the Elsevier `elsarticle` submission source.
- `figures/` contains the generated, data-free schematics and evidence charts.
- `HIGHLIGHTS.md` and `CREDIT.md` contain companion submission materials.

SoftwareX requires its journal-specific Word or LaTeX template for submission.
The LaTeX source uses Elsevier's `elsarticle` class and numeric bibliography
style. The custom PDF remains useful for review because it is reproducibly
generated from the Markdown source with its figures embedded.

To rebuild it without modifying the application environment:

```bash
python3 -m pip install matplotlib markdown weasyprint
python3 paper/build_manuscript.py
```

The builder generates synthetic schematics and composes the worked-example
figure from checked-in synthetic screenshots. Chart counts correspond to the
checked-in quantitative summary. It does not rerun experiments or read patient
images, local caches, or private data.

To regenerate and compile the submission source with Tectonic (which provides
the Elsevier `elsarticle` class), run from the repository root:

```bash
python3 paper/build_submission.py --compile
```

The builder compiles in a temporary directory and writes
`softwarex-submission.pdf`, leaving the illustrated review PDF intact.
Both PDFs derive from the same Markdown manuscript; neither is a placeholder.
The retired planning scaffold in `docs/SOFTWARE_PAPER.md` now points here.

Count the manuscript text and run the submission-generation checks:

```bash
python3 paper/check_manuscript.py
python3 -m unittest discover -s paper -p 'test_*.py'
```

To check PDF text, reference capitalization, links, and embedded figures, and
render visual proofs (requires `pymupdf` and `pillow`):

```bash
python3 paper/check_pdf.py /tmp/corridorkit-proof
```

CorridorKit version 0.3.0 uses `corridorkit` for the repository, Python package,
imports, and CLI; the desktop command is `corridorkit-gui`. Historical releases
retain their original names and identifiers.

Release verification runs from a clean detached checkout of the tag:

```bash
python paper/verify_release.py --tag v0.3.0 \
  --slicer "$HOME/Applications/Slicer-5.12.3.app/Contents/MacOS/Slicer" \
  --output /tmp/corridorkit-v0.3.0-verification
```

Use an interpreter with the project installed and its desktop/test/build
dependencies available. The runner checks the source and wheel, builds the
Slicer package, verifies the synthetic worked example, and runs native Slicer
integration. Its output directory must not already exist.

Before upload, all authors should approve the author order, CRediT statement,
affiliation, ethics wording, funding declaration, and competing-interest
declaration. Add ORCID identifiers in the submission system only when verified.
