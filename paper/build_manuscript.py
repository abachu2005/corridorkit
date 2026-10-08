"""Build the illustrated SoftwareX draft as HTML and PDF.

Figures are generated from synthetic geometry or repository evidence only.
No patient images or private data are used.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Rectangle
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper"
FIGURES = PAPER / "figures"
EVIDENCE = ROOT / "docs/evidence/multicase-integrated-acceptance-20261008.json"

BLUE = "#176B87"
TEAL = "#2A9D8F"
GOLD = "#E9C46A"
ORANGE = "#F4A261"
RED = "#D65A4A"
DARK = "#263238"
LIGHT = "#EAF3F5"


def save(fig: plt.Figure, name: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / name, dpi=240, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def architecture_figure() -> None:
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.set_xlim(0, 11)
    ax.set_ylim(0, 4.2)
    ax.axis("off")
    boxes = [
        (0.25, 1.25, 1.75, 1.55, "3D Slicer", "CT, markups,\nreviewed segments"),
        (2.55, 1.25, 1.8, 1.55, "Local worker", "TotalSegmentator\n(predictions)"),
        (4.9, 1.25, 1.8, 1.55, "Typed engine", "finite capsules,\nportal checks"),
        (7.25, 1.25, 1.65, 1.55, "Analysis", "coverage sets,\nabstention"),
        (9.45, 1.25, 1.35, 1.55, "Outputs", "scene, masks,\nJSON report"),
    ]
    for x, y, w, h, title, body in boxes:
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.04,rounding_size=0.08",
            facecolor=LIGHT, edgecolor=BLUE, linewidth=1.6
        ))
        ax.text(x + w / 2, y + 1.08, title, ha="center", va="center",
                fontsize=11, fontweight="bold", color=DARK)
        ax.text(x + w / 2, y + 0.52, body, ha="center", va="center",
                fontsize=9, color=DARK)
    for left, right in zip(boxes, boxes[1:]):
        x1 = left[0] + left[2]
        x2 = right[0]
        ax.add_patch(FancyArrowPatch(
            (x1 + 0.05, 2.02), (x2 - 0.05, 2.02), arrowstyle="-|>",
            mutation_scale=13, linewidth=1.4, color=TEAL
        ))
    ax.text(5.5, 3.65, "Public execution boundary: local workstation",
            ha="center", fontsize=13, fontweight="bold", color=BLUE)
    ax.add_patch(Rectangle((0.08, 0.7), 10.84, 2.65, fill=False,
                           linestyle="--", linewidth=1.4, edgecolor=TEAL))
    ax.text(5.5, 0.25,
            "The optional Azure backend is evaluation infrastructure and is not required by the public workflow.",
            ha="center", fontsize=9, color=DARK)
    save(fig, "figure-1-local-first-architecture.png")


def geometry_figure() -> None:
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(11, 4.4))
    for panel in (ax, bx):
        panel.set_aspect("equal")
        panel.axis("off")

    ax.set_xlim(-1.2, 8.7)
    ax.set_ylim(-2.4, 2.8)
    ax.plot([0, 0], [-2.0, 2.0], color=DARK, linewidth=2)
    ax.add_patch(Circle((0, 0), 1.15, fill=False, linewidth=2, edgecolor=BLUE))
    ax.plot([0, 7], [0, 1.25], color=TEAL, linewidth=9, solid_capstyle="round")
    ax.plot([0, 7], [0, 1.25], color="white", linewidth=1.2, alpha=0.9)
    ax.add_patch(Circle((7.45, 1.33), 0.65, facecolor=GOLD, edgecolor=DARK))
    ax.annotate("finite portal", xy=(0.1, -1.15), xytext=(1.2, -2.0),
                arrowprops={"arrowstyle": "->", "color": DARK}, fontsize=9)
    ax.annotate("finite swept capsule", xy=(3.8, 0.68), xytext=(3.0, 2.15),
                arrowprops={"arrowstyle": "->", "color": DARK}, fontsize=9)
    ax.annotate("target sample", xy=(7.45, 1.33), xytext=(6.0, -1.0),
                arrowprops={"arrowstyle": "->", "color": DARK}, fontsize=9)
    ax.set_title("A  Finite-instrument model", loc="left", fontweight="bold", color=DARK)

    bx.set_xlim(-0.8, 8.8)
    bx.set_ylim(-2.4, 2.8)
    bx.add_patch(Circle((0, 0), 0.25, facecolor=BLUE, edgecolor=DARK))
    bx.plot([0, 7.5], [0, 0.9], color=TEAL, linewidth=5, solid_capstyle="round")
    bx.add_patch(Circle((4.0, 0.48), 1.05, facecolor=RED, edgecolor=DARK, alpha=0.82))
    bx.add_patch(Circle((7.5, 0.9), 0.62, facecolor=GOLD, edgecolor=DARK))
    bx.annotate("entry e", xy=(0, 0), xytext=(0.15, -1.35),
                arrowprops={"arrowstyle": "->", "color": DARK}, fontsize=9)
    bx.annotate("protected structure", xy=(4.0, 0.48), xytext=(3.0, 2.15),
                arrowprops={"arrowstyle": "->", "color": DARK}, fontsize=9)
    bx.annotate("blocked path", xy=(4.0, 0.48), xytext=(5.15, -1.25),
                arrowprops={"arrowstyle": "->", "color": DARK}, fontsize=9)
    bx.set_title("B  Exact-path state reporting", loc="left", fontweight="bold", color=DARK)
    fig.suptitle("Synthetic schematic (not patient anatomy; not to scale)",
                 fontsize=12, fontweight="bold", color=BLUE)
    save(fig, "figure-2-finite-instrument-geometry.png")


def evidence_figure() -> None:
    evidence = json.loads(EVIDENCE.read_text())
    cases = evidence["cases"]
    labels = [case["case"] for case in cases]
    times = [case["elapsed_seconds"] for case in cases]
    blocked = [case["states"].get("blocked", 0) for case in cases]
    unavailable = [case["states"].get("unavailable", 0) for case in cases]

    fig, axes = plt.subplots(1, 3, figsize=(11.5, 4.0))
    ax, bx, cx = axes
    bars = ax.bar(labels, times, color=[TEAL, ORANGE, BLUE], width=0.62)
    for bar, value in zip(bars, times):
        ax.text(bar.get_x() + bar.get_width() / 2, value + 10, f"{value:g} s",
                ha="center", fontsize=9)
    ax.set_ylim(0, max(times) * 1.2)
    ax.set_ylabel("Elapsed seconds")
    ax.set_title("A  Optional Azure benchmark", loc="left", fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)

    x = np.arange(len(labels))
    bx.bar(x, blocked, color=RED, label="blocked")
    bx.bar(x, unavailable, bottom=blocked, color=GOLD, label="unavailable")
    bx.set_xticks(x, labels)
    bx.set_ylim(0, 3.6)
    bx.set_yticks([0, 1, 2, 3])
    bx.set_ylabel("Candidate paths")
    bx.set_title("B  Retained path states", loc="left", fontweight="bold")
    bx.legend(frameon=False, loc="upper left")
    bx.spines[["top", "right"]].set_visible(False)

    eligible, excluded = 118, 12
    cx.pie([eligible, excluded], colors=[TEAL, ORANGE], startangle=90,
           wedgeprops={"width": 0.38, "edgecolor": "white"})
    cx.text(0, 0.08, "130", ha="center", va="center", fontsize=20,
            fontweight="bold", color=DARK)
    cx.text(0, -0.2, "paired records", ha="center", va="center", fontsize=8, color=DARK)
    cx.set_title("C  NasalSeg geometry audit", loc="left", fontweight="bold")
    cx.legend([f"eligible: {eligible}", f"excluded: {excluded}"],
              loc="lower center", bbox_to_anchor=(0.5, -0.18), frameon=False, fontsize=8)
    fig.text(0.5, -0.04,
             "Engineering execution evidence only; no operative-corridor ground truth or clinical validation.",
             ha="center", fontsize=9, color=DARK)
    fig.tight_layout()
    save(fig, "figure-3-public-evaluation-summary.png")


def bibliography_html() -> str:
    return """
<ol class="references">
  <li id="ref-slicer">Fedorov A, Beichel R, Kalpathy-Cramer J, et al. 3D Slicer as an
  image computing platform for the Quantitative Imaging Network.
  <em>Magnetic Resonance Imaging</em>. 2012;30(9):1323–1341.
  doi:10.1016/j.mri.2012.05.001.</li>
  <li id="ref-totalsegmentator">Wasserthal J, Breit H-C, Meyer MT, et al.
  TotalSegmentator: robust segmentation of 104 anatomic structures in CT images.
  <em>Radiology: Artificial Intelligence</em>. 2023;5(5):e230024.
  doi:10.1148/ryai.230024.</li>
  <li id="ref-nasalseg">NasalSeg dataset, version record 13893419. Zenodo.
  <a href="https://zenodo.org/records/13893419">https://zenodo.org/records/13893419</a>
  (accessed 8 October 2026).</li>
</ol>
"""


def build_document() -> None:
    import markdown
    from weasyprint import HTML

    source = (PAPER / "softwarex.md").read_text()
    source = source.replace("[@slicer]", "[1]")
    source = source.replace("[@totalsegmentator]", "[2]")
    source = source.replace("[@nasalseg]", "[3]")
    source = source.replace(r"\(\mathbf{e}\)", "<strong>e</strong>")
    source = source.replace(r"\(\mathbf{d}\)", "<strong>d</strong>")
    source = source.replace(r"\(\mathbf{x}\)", "<strong>x</strong>")
    source = re.sub(
        r"\\\[\s*t=\(\\mathbf\{x\}-\\mathbf\{e\}\)\\cdot\\mathbf\{d\},\s*\\\]",
        '<div class="equation"><em>t</em> = (<strong>x</strong> − <strong>e</strong>) '
        '· <strong>d</strong></div>',
        source,
    )
    source = re.sub(
        r"\\\[\s*\\left\\\|\(\\mathbf\{x\}-\\mathbf\{e\}\)-t\\mathbf\{d\}\\right\\\|\.\s*\\\]",
        '<div class="equation">‖(<strong>x</strong> − <strong>e</strong>) − '
        '<em>t</em><strong>d</strong>‖.</div>',
        source,
    )
    source = re.sub(r"## References\s*$", "## References\n\n" + bibliography_html(),
                    source, flags=re.MULTILINE)
    body = markdown.markdown(
        source,
        extensions=["extra", "smarty", "sane_lists"],
        output_format="html5",
    )
    css = """
@page { size: A4; margin: 18mm 18mm 20mm 18mm;
  @bottom-center { content: counter(page); font: 9pt Arial; color: #546e7a; } }
body { font-family: "Times New Roman", serif; font-size: 10pt; line-height: 1.38;
  color: #1f2933; }
h1 { font: bold 19pt Arial, sans-serif; color: #134e66; line-height: 1.15; }
h2 { font: bold 14pt Arial, sans-serif; color: #176b87; margin-top: 18pt;
  break-after: avoid; }
h3 { font: bold 11.5pt Arial, sans-serif; color: #263238; margin-top: 12pt; }
p { text-align: justify; orphans: 3; widows: 3; }
li { margin-bottom: 2.5pt; }
code { font: 8.5pt Menlo, monospace; color: #37474f; }
pre { background: #f4f7f8; border-left: 3px solid #2a9d8f; padding: 8pt;
  white-space: pre-wrap; }
a { color: #176b87; text-decoration: none; }
.figure { break-inside: avoid; margin: 13pt 0 16pt; text-align: center; }
.figure img { max-width: 100%; max-height: 185mm; }
.caption { font-size: 9pt; text-align: left; margin: 5pt 10pt; line-height: 1.3; }
.draft-banner { border: 1.2px solid #d65a4a; color: #8b2f25; background: #fff4f1;
  padding: 7pt; font: bold 9pt Arial, sans-serif; text-align: center; margin-bottom: 12pt; }
.equation { text-align: center; font-style: italic; margin: 9pt; }
.references { font-size: 9pt; break-before: avoid; }
blockquote { color: #455a64; border-left: 3px solid #b0bec5; padding-left: 10pt; }
"""
    banner = (
        '<div class="draft-banner">SUBMISSION DRAFT — author affiliation, email, ORCID, '
        "funding, competing-interest, acknowledgement, and ethics metadata remain to be verified."
        "</div>"
    )
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>skullbase-corridor "
        f"SoftwareX draft</title><style>{css}</style></head><body>{banner}{body}</body></html>"
    )
    html_path = PAPER / "softwarex.html"
    html_path.write_text(html)
    HTML(string=html, base_url=str(PAPER)).write_pdf(PAPER / "softwarex.pdf")


def main() -> None:
    architecture_figure()
    geometry_figure()
    evidence_figure()
    build_document()
    print(PAPER / "softwarex.pdf")


if __name__ == "__main__":
    main()
