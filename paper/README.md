# SoftwareX manuscript

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

The builder uses only synthetic geometry and the checked-in multi-case evidence
ledger. It does not read patient images, local caches, or private data.

To compile the submission source with a TeX installation that provides
`elsarticle`:

```bash
cd paper
tectonic softwarex.tex
```

Before upload, all authors should approve the author order, CRediT statement,
affiliation, ethics wording, funding declaration, and competing-interest
declaration. Add ORCID identifiers in the submission system only when verified.
