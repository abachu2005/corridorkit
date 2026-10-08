"""Build the illustrated SoftwareX review manuscript as HTML and PDF.

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
    ax.text(5.5, 3.65, "Software components and data flow",
            ha="center", fontsize=13, fontweight="bold", color=BLUE)
    ax.add_patch(Rectangle((0.08, 0.7), 10.84, 2.65, fill=False,
                           linestyle="--", linewidth=1.4, edgecolor=TEAL))
    ax.text(5.5, 0.25,
            "Reviewed structures flow through explicit geometry, analysis, and export stages.",
            ha="center", fontsize=9, color=DARK)
    save(fig, "figure-1-software-architecture.png")


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


def worked_example_figure() -> None:
    from PIL import Image, ImageEnhance, ImageOps

    directory = FIGURES / "worked-example"
    images = [
        Image.open(directory / "planning-workspace.png").convert("RGB"),
        Image.open(directory / "eea-path.png").convert("RGB"),
        Image.open(directory / "transmaxillary-path.png").convert("RGB"),
    ]
    gamma_lut = [round(255 * ((value / 255) ** 0.62)) for value in range(256)]
    images = [
        ImageEnhance.Contrast(
            ImageOps.autocontrast(image).point(gamma_lut * 3)
        ).enhance(1.05)
        for image in images
    ]
    fig, axes = plt.subplots(2, 2, figsize=(12.0, 8.6), facecolor="white")
    titles = [
        "A  Planning workspace",
        "B  Portal A trajectory (nominal EEA)",
        "C  Portal B trajectory (nominal transmaxillary)",
    ]
    for ax, image, title in zip(axes.flat[:3], images, titles):
        ax.imshow(image)
        ax.set_title(title, loc="left", fontsize=10, fontweight="bold")
        ax.axis("off")
    chart = axes.flat[3]
    categories = ["Portal A only", "Portal B only", "Both portals", "Not reached"]
    counts = [59, 232, 35, 87]
    colors = [BLUE, ORANGE, TEAL, "#9AA6AC"]
    bars = chart.barh(categories, counts, color=colors, edgecolor="white", height=0.64)
    chart.invert_yaxis()
    chart.set_xlim(0, 260)
    chart.set_xlabel("Target samples (n = 413)", fontsize=9)
    chart.set_title("D  Sampled coverage comparison", loc="left",
                    fontsize=10, fontweight="bold")
    chart.spines[["top", "right", "left"]].set_visible(False)
    chart.grid(axis="x", color="#D8E1E5", linewidth=0.8)
    chart.set_axisbelow(True)
    chart.tick_params(axis="y", labelsize=9, length=0)
    chart.tick_params(axis="x", labelsize=8)
    for bar, count in zip(bars, counts):
        chart.text(count + 5, bar.get_y() + bar.get_height() / 2, str(count),
                   va="center", fontsize=9, fontweight="bold", color=DARK)
    chart.text(0.0, -0.22, "Invented geometry; nominal approach labels",
               transform=chart.transAxes, fontsize=8.5, color=DARK)
    fig.suptitle("Deterministic positive synthetic worked example",
                 fontsize=13, fontweight="bold", color=BLUE)
    fig.tight_layout(rect=(0, 0.02, 1, 0.96), h_pad=2.0, w_pad=2.0)
    save(fig, "figure-3-positive-worked-example.png")


def bibliography_html() -> str:
    return """
<ol class="references">
  <li id="ref-slicer">Fedorov A, Beichel R, Kalpathy-Cramer J, et al. 3D Slicer as an
  image computing platform for the Quantitative Imaging Network.
  <em>Magnetic Resonance Imaging</em>. 2012;30(9):1323–1341.
  doi:10.1016/j.mri.2012.05.001.</li>
  <li>Ungi T, Lasso A, Fichtinger G. Open-source platforms for navigated
  image-guided interventions. <em>Medical Image Analysis</em>. 2016;33:181–186.
  doi:10.1016/j.media.2016.06.011.</li>
  <li>Yeniaras E, Fuentes DT, Fahrenholtz SJ, et al. Design and initial evaluation
  of a treatment planning software system for MRI-guided laser ablation in the
  brain. <em>Int J CARS</em>. 2014;9:659–667. doi:10.1007/s11548-013-0948-x.</li>
  <li>Muralidharan V, Swaminathan G, Devadhas D, Joseph BV. Patient-specific
  interactive software module for virtual preoperative planning and visualization
  of pedicle screw entry point and trajectories. <em>Neurology India</em>.
  2018;66(6):1766–1770. doi:10.4103/0028-3886.246281.</li>
  <li>Chen X, Xu L, Wang H, et al. Development of a surgical navigation system
  based on 3D Slicer for intraoperative implant placement surgery.
  <em>Medical Engineering &amp; Physics</em>. 2017;41:81–89.
  doi:10.1016/j.medengphy.2017.01.005.</li>
  <li>Narizzano M, Arnulfo G, Ricci S, et al. SEEG assistant: a 3DSlicer extension
  to support epilepsy surgery. <em>BMC Bioinformatics</em>. 2017;18:124.
  doi:10.1186/s12859-017-1545-8.</li>
  <li>Brunenberg EJL, Vilanova A, Visser-Vandewalle V, et al. Automatic trajectory
  planning for deep brain stimulation: a feasibility study. <em>MICCAI</em>.
  2007:584–592. doi:10.1007/978-3-540-75757-3_71.</li>
  <li>Bériault S, Al Subaie F, Collins DL, Sadikot AF, Pike GB. A multi-modal
  approach to computer-assisted deep brain stimulation trajectory planning.
  <em>Int J CARS</em>. 2012;7(5):687–704. doi:10.1007/s11548-012-0768-4.</li>
  <li>Shamir RR, Joskowicz L, Tamir I, et al. Reduced risk trajectory planning in
  image-guided keyhole neurosurgery. <em>Medical Physics</em>.
  2012;39(5):2885–2895. doi:10.1118/1.4704643.</li>
  <li>Trope M, Shamir RR, Joskowicz L, et al. The role of automatic computer-aided
  surgical trajectory planning in improving the expected safety of stereotactic
  neurosurgery. <em>Int J CARS</em>. 2015;10(7):1127–1140.
  doi:10.1007/s11548-014-1126-5.</li>
  <li>Sparks R, Vakharia V, Rodionov R, et al. Anatomy-driven multiple trajectory
  planning of intracranial electrodes for epilepsy surgery. <em>Int J CARS</em>.
  2017;12(8):1245–1255. doi:10.1007/s11548-017-1628-z.</li>
  <li>Wankhede A, Madiraju L, Siampli E, et al. Validation of a novel path planner
  for stereotactic neurosurgical interventions. <em>Int J Med Robotics Comput
  Assist Surg</em>. 2022;18(6):e2458. doi:10.1002/rcs.2458.</li>
  <li>Roth J, Singh A, Nyquist G, et al. Three-dimensional and 2-dimensional
  endoscopic exposure of midline cranial base targets. <em>Neurosurgery</em>.
  2009;65(6):1116–1128. doi:10.1227/01.NEU.0000360340.85186.7A.</li>
  <li>Wilson DA, Williamson RW, Preul MC, Little AS. Comparative analysis of
  surgical freedom and angle of attack of two minimal-access endoscopic
  transmaxillary approaches. <em>World Neurosurgery</em>. 2014;82:e487–e493.
  doi:10.1016/j.wneu.2013.02.003.</li>
  <li>Elhadi AM, Almefty KK, Mendes GAC, et al. Comparison of surgical freedom and
  area of exposure in three endoscopic transmaxillary approaches.
  <em>J Neurol Surg B Skull Base</em>. 2014;75(5):346–353.
  doi:10.1055/s-0034-1372467.</li>
  <li>Lin B-J, Ju D-T, Hsu T-H, et al. Quantitative comparison of endoscopically
  assisted endonasal, sublabial and transorbital transmaxillary approaches.
  <em>Clinical Otolaryngology</em>. 2021;46(1):123–130. doi:10.1111/coa.13559.</li>
  <li>Agosti E, Saraceno G, Rampinelli V, et al. Quantitative anatomic comparison
  of endoscopic transnasal and microsurgical transcranial approaches.
  <em>Operative Neurosurgery</em>. 2022;23(4):e256–e266.
  doi:10.1227/ons.0000000000000312.</li>
  <li id="ref-totalsegmentator">Wasserthal J, Breit H-C, Meyer MT, et al.
  TotalSegmentator: robust segmentation of 104 anatomic structures in CT images.
  <em>Radiology: Artificial Intelligence</em>. 2023;5(5):e230024.
  doi:10.1148/ryai.230024.</li>
  <li>Isensee F, Jaeger PF, Kohl SAA, Petersen J, Maier-Hein KH. nnU-Net: a
  self-configuring method for deep learning-based biomedical image segmentation.
  <em>Nature Methods</em>. 2021;18(2):203–211.
  doi:10.1038/s41592-020-01008-z.</li>
  <li id="ref-nasalseg">Zhang Y, Wang J, Pan T, et al. NasalSeg Dataset for
  Nasal Cavity and Paranasal Sinuses Segmentation from CT Images. Zenodo. 2024.
  doi:10.5281/zenodo.13893419.</li>
</ol>
"""


def build_document() -> None:
    import markdown
    from weasyprint import HTML

    source = (PAPER / "softwarex.md").read_text()
    citation_numbers = {
        "slicer": 1, "ungi2016": 2, "yeniaras2014": 3, "muralidharan2018": 4,
        "chen2017": 5, "narizzano2017": 6, "brunenberg2007": 7,
        "beriault2012": 8, "shamir2012": 9, "trope2015": 10, "sparks2017": 11,
        "wankhede2022": 12, "roth2009": 13, "wilson2014": 14, "elhadi2014": 15,
        "lin2021": 16, "agosti2022": 17, "totalsegmentator": 18, "nnunet": 19,
        "nasalseg": 20,
    }

    def replace_citations(match: re.Match[str]) -> str:
        keys = re.findall(r"@([A-Za-z0-9_-]+)", match.group(0))
        return "[" + ", ".join(str(citation_numbers[key]) for key in keys) + "]"

    source = re.sub(r"\[(?:@[A-Za-z0-9_-]+(?:;\s*)?)+\]", replace_citations, source)
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
table { width: 100%; border-collapse: collapse; table-layout: fixed;
  font-size: 9pt; margin: 10pt 0; }
th, td { text-align: left; vertical-align: top; padding: 6pt 9pt;
  border-bottom: 0.5pt solid #d8e1e5; overflow-wrap: anywhere; }
th:first-child, td:first-child { width: 29%; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
.figure { break-inside: avoid; margin: 13pt 0 16pt; text-align: center; }
.figure img { max-width: 100%; max-height: 185mm; }
.caption { font-size: 9pt; text-align: left; margin: 5pt 10pt; line-height: 1.3; }
.equation { text-align: center; font-style: italic; margin: 9pt; }
.references { font-size: 9pt; break-before: avoid; }
blockquote { color: #455a64; border-left: 3px solid #b0bec5; padding-left: 10pt; }
"""
    html = (
        "<!doctype html><html><head><meta charset='utf-8'><title>CorridorKit "
        f"SoftwareX manuscript</title><style>{css}</style></head><body>{body}</body></html>"
    )
    html_path = PAPER / "softwarex.html"
    html_path.write_text(html)
    HTML(string=html, base_url=str(PAPER)).write_pdf(PAPER / "softwarex.pdf")


def main() -> None:
    architecture_figure()
    geometry_figure()
    worked_example_figure()
    build_document()
    print(PAPER / "softwarex.pdf")


if __name__ == "__main__":
    main()
