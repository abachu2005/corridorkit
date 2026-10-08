# SoftwareX manuscript

- `softwarex.pdf` is the illustrated submission draft.
- `softwarex.md` is the editable manuscript source.
- `figures/` contains the generated, data-free schematics and evidence charts.
- `HIGHLIGHTS.md` and `CREDIT.md` contain companion submission materials.

The PDF is visibly marked as a draft because author affiliation, email, ORCID,
funding, competing-interest, acknowledgement, and ethics metadata still require
verification.

To rebuild it without modifying the application environment:

```bash
python3 -m pip install matplotlib markdown weasyprint
python3 paper/build_manuscript.py
```

The builder uses only synthetic geometry and the checked-in multi-case evidence
ledger. It does not read patient images, local caches, or private data.
