"""Generate the Elsevier/SoftwareX LaTeX submission source from softwarex.md."""
from __future__ import annotations

import re
from pathlib import Path

PAPER = Path(__file__).resolve().parent
SOURCE = PAPER / "softwarex.md"
OUTPUT = PAPER / "softwarex.tex"


def section(text: str, heading: str, next_heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\s*$\n(.*?)(?=^## {re.escape(next_heading)}\s*$)"
    match = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError(f"Could not find section {heading!r}")
    return match.group(1).strip()


def latex_escape(value: str) -> str:
    return (
        value.replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("$", r"\$")
        .replace("#", r"\#")
        .replace("_", r"\_")
        .replace("{", r"\{")
        .replace("}", r"\}")
    )


def inline(value: str) -> str:
    tokens: list[str] = []

    def token(content: str) -> str:
        tokens.append(content)
        return f"ZZTOKEN{len(tokens) - 1}ZZ"

    value = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: token(r"\href{" + m.group(2) + "}{" + latex_escape(m.group(1)) + "}"),
        value,
    )
    value = re.sub(
        r"\[@([^\]]+)\]",
        lambda m: token(
            r"\citep{" + ",".join(part.strip().lstrip("@") for part in m.group(1).split(";")) + "}"
        ),
        value,
    )
    value = re.sub(
        r"`([^`]+)`",
        lambda m: token(r"\texttt{" + latex_escape(m.group(1)) + "}"),
        value,
    )
    value = re.sub(
        r"\*\*([^*]+)\*\*",
        lambda m: token(r"\textbf{" + latex_escape(m.group(1)) + "}"),
        value,
    )
    value = latex_escape(value)
    for index, content in enumerate(tokens):
        value = value.replace(f"ZZTOKEN{index}ZZ", content)
    return value.replace("—", "---").replace("–", "--").replace("≥", r"$\geq$")


def convert_table(lines: list[str]) -> str:
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines]
    rows = [row for index, row in enumerate(rows) if index != 1]
    width = len(rows[0])
    columns = "p{0.25\\linewidth}" + "p{0.68\\linewidth}" * (width - 1)
    output = [rf"\begin{{longtable}}{{{columns}}}", r"\toprule"]
    for index, row in enumerate(rows):
        output.append(" & ".join(inline(cell) for cell in row) + r" \\")
        output.append(r"\midrule" if index == 0 else "")
    output.extend([r"\bottomrule", r"\end{longtable}"])
    return "\n".join(output)


def markdown_to_latex(body: str) -> str:
    figure_pattern = re.compile(
        r'<div class="figure">\s*'
        r'<img src="([^"]+)" alt="[^"]*">\s*'
        r'<p class="caption"><strong>Figure \d+\.</strong>\s*(.*?)</p>\s*'
        r"</div>",
        flags=re.DOTALL,
    )

    def replace_figure(match: re.Match[str]) -> str:
        caption = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        return (
            "\nFIGURESTART\n"
            + match.group(1)
            + "\nFIGURECAPTION\n"
            + caption
            + "\nFIGUREEND\n"
        )

    body = figure_pattern.sub(replace_figure, body)
    body = re.sub(r"^## References\s*$.*", "", body, flags=re.MULTILINE | re.DOTALL)
    lines = body.strip().splitlines()
    output: list[str] = []
    paragraph: list[str] = []
    index = 0

    def flush() -> None:
        if paragraph:
            output.append(inline(" ".join(item.strip() for item in paragraph)))
            output.append("")
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        if line.startswith("FIGURESTART"):
            flush()
            path = lines[index + 1]
            caption = lines[index + 3]
            output.extend(
                [
                    r"\begin{figure}[htbp]",
                    r"\centering",
                    rf"\includegraphics[width=\linewidth]{{{path}}}",
                    rf"\caption{{{inline(caption)}}}",
                    r"\end{figure}",
                    "",
                ]
            )
            index += 5
            continue
        if line.startswith("```"):
            flush()
            code: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                code.append(lines[index])
                index += 1
            output.extend([r"\begin{verbatim}", *code, r"\end{verbatim}", ""])
            index += 1
            continue
        if line.startswith("|"):
            flush()
            table: list[str] = []
            while index < len(lines) and lines[index].startswith("|"):
                table.append(lines[index])
                index += 1
            output.extend([convert_table(table), ""])
            continue
        heading = re.match(r"^(#{2,3})\s+(.*)$", line)
        if heading:
            flush()
            command = "section" if len(heading.group(1)) == 2 else "subsection"
            title = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", heading.group(2))
            output.append(rf"\{command}{{{inline(title)}}}")
            output.append("")
        elif line.startswith(r"\["):
            flush()
            equation: list[str] = [line]
            while not equation[-1].rstrip().endswith(r"\]"):
                index += 1
                equation.append(lines[index])
            output.extend(equation + [""])
        elif not line.strip():
            flush()
        else:
            paragraph.append(line)
        index += 1
    flush()
    return "\n".join(output)


def main() -> None:
    source = SOURCE.read_text()
    abstract = section(source, "Abstract", "Keywords")
    keywords = section(source, "Keywords", "Code metadata (mandatory)")
    body_start = source.index("## Code metadata (mandatory)")
    converted = markdown_to_latex(source[body_start:])

    tex = rf"""\documentclass[preprint,12pt]{{elsarticle}}

\usepackage{{amsmath,amssymb}}
\usepackage{{booktabs,longtable,array}}
\usepackage{{graphicx}}
\usepackage{{hyperref}}
\usepackage{{microtype}}
\usepackage{{natbib}}
\usepackage{{url}}
\graphicspath{{{{figures/}}}}

\journal{{SoftwareX}}

\begin{{document}}
\begin{{frontmatter}}

\title{{skullbase-corridor: Finite-instrument corridor geometry analysis for skull-base research in 3D Slicer}}

\author[luc]{{Abhinav Bachu\corref{{cor1}}}}
\ead{{abachu@luc.edu}}
\author[luc]{{Raghav Rajesh}}
\author[luc]{{Anand V. Germanwala}}
\cortext[cor1]{{Corresponding author and software support contact}}
\affiliation[luc]{{organization={{Department of Neurological Surgery, Loyola University Chicago Stritch School of Medicine}},
  city={{Maywood}},
  state={{Illinois}},
  country={{USA}}}}

\begin{{abstract}}
{abstract}
\end{{abstract}}

\begin{{keyword}}
{latex_escape(keywords).replace("; ", r" \sep ")}
\end{{keyword}}

\end{{frontmatter}}

{converted}

\bibliographystyle{{elsarticle-num}}
\bibliography{{references}}

\end{{document}}
"""
    OUTPUT.write_text(tex)
    print(OUTPUT)


if __name__ == "__main__":
    main()
