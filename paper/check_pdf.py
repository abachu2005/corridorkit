"""Check compiled PDF text and render review images (requires PyMuPDF).

Usage: python3 paper/check_pdf.py /tmp/corridorkit-proof
"""
from __future__ import annotations

import sys
from pathlib import Path

import fitz

PAPER = Path(__file__).resolve().parent


def check_pdf(path: Path) -> fitz.Document:
    document = fitz.open(path)
    text = "\n".join(page.get_text() for page in document)
    assert "CorridorKit" in text
    for expected in ("MRI-guided", "3D Slicer", "SEEG", "3DSlicer", "nnU-Net"):
        assert expected in text, f"{path.name}: missing {expected}"
    for broken in ("LicenseApache", "usedPython", "surgeonsupervised", "skullbasecorridor/issues"):
        assert broken not in text, f"{path.name}: broken text {broken}"
    for field in range(1, 10):
        assert f"C{field} " in text, f"Missing C{field}"
    links = [link.get("uri", "") for page in document for link in page.get_links()]
    assert "https://github.com/abachu2005/corridorkit/issues" in links
    assert not any("github.com/abachu2005/skullbase-corridor" in link for link in links)
    assert sum(len(page.get_images()) for page in document) >= 3
    print(f"{path.name}: {len(document)} pages; text, links, and figures passed")
    return document


def main() -> None:
    submission = check_pdf(PAPER / "softwarex-submission.pdf")
    assert "ADMTP" in "\n".join(page.get_text() for page in submission)
    review = check_pdf(PAPER / "softwarex.pdf")
    if len(sys.argv) > 1:
        from PIL import Image, ImageOps, ImageDraw

        directory = Path(sys.argv[1])
        directory.mkdir(parents=True, exist_ok=True)
        thumbs = []
        for index, page in enumerate(submission):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(0.65, 0.65))
            image = Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)
            image = ImageOps.expand(image, border=8, fill="#d8d8d8")
            ImageDraw.Draw(image).text((12, 10), f"Page {index + 1}", fill="black")
            thumbs.append(image)
            if index < 3 or "MRI-guided" in page.get_text() or "nnU-Net" in page.get_text():
                page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(directory / f"submission-{index + 1}.png")
        width, height = max(i.width for i in thumbs), max(i.height for i in thumbs)
        sheet = Image.new("RGB", (width * 4, height * ((len(thumbs) + 3) // 4)), "white")
        for index, image in enumerate(thumbs):
            sheet.paste(image, ((index % 4) * width, (index // 4) * height))
        sheet.save(directory / "submission-contact-sheet.png")
        review[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(directory / "review-1.png")
        print(f"Visual proofs: {directory}")


if __name__ == "__main__":
    main()
