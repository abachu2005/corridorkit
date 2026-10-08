# SoftwareX manuscript

- `softwarex.pdf` is the illustrated internal-review draft.
- `softwarex.md` is the editable manuscript source.
- `figures/` contains the generated, data-free schematics and evidence charts.
- `HIGHLIGHTS.md` and `CREDIT.md` contain companion submission materials.

The PDF is visibly marked as a draft because author affiliation, email, ORCID,
funding, competing-interest, acknowledgement, and ethics metadata still require
verification.

SoftwareX requires its journal-specific Word or LaTeX template for submission.
This custom PDF is not the submission source. Migrate the settled text, C1--C9
metadata table, references, and figures into the official template after the
remaining runtime evidence and author metadata are complete.

To rebuild it without modifying the application environment:

```bash
python3 -m pip install matplotlib markdown weasyprint
python3 paper/build_manuscript.py
```

The builder uses only synthetic geometry and the checked-in multi-case evidence
ledger. It does not read patient images, local caches, or private data.
