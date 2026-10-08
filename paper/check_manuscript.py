"""Count manuscript words and check submission-source consistency."""
from __future__ import annotations

import re
from pathlib import Path

from build_submission import section

PAPER = Path(__file__).resolve().parent


def words(text: str) -> int:
    """Count whitespace-separated words, preserving hyphenated compounds.

    Include headings and captions. Remove markup, citation keys, code fences,
    and display equations, which are not prose. Inline code and link labels
    remain in the count.
    """
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"\\\[.*?\\\]", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[@[^\]]+\]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(?m)^#+\s*", "", text)
    text = re.sub(r"[*`]", "", text)
    return sum(bool(re.search(r"\w", word)) for word in text.split())


def check() -> None:
    source = (PAPER / "softwarex.md").read_text()
    abstract = section(source, "Abstract", "Keywords")
    body = source[source.index("## 1. Motivation and significance\n"):]
    body = body.split("## References", 1)[0]
    counts = {
        "Abstract": words(abstract),
        "Numbered body (including captions and declarations)": words(body),
        "Abstract plus numbered body": words(abstract) + words(body),
    }
    for label, count in counts.items():
        print(f"{label}: {count} words")
    assert counts["Abstract"] <= 100, "Abstract exceeds the conservative 100-word cap"
    assert counts["Abstract plus numbered body"] <= 3000, "Text exceeds 3,000 words"
    assert "clinically validated" not in abstract
    assert source.startswith("# CorridorKit:")
    assert "skullbase-corridor" not in source
    assert "skullbase_corridor" not in source
    assert "| C1 Current code version | `v0.3.0` |" in source
    c2 = next(line for line in source.splitlines() if line.startswith("| C2 "))
    for url in ("https://github.com/abachu2005/corridorkit",
                "https://doi.org/10.5281/zenodo.23249277"):
        assert f"[{url}]({url})" in c2, "C2 must display the actual URL"
    assert not re.search(r"\b(TODO|TBD|FIXME|XXX|placeholder)\b", source, re.I)
    assert not re.search(r"\b(millimetres|centres|tumour|centre-entry)\b", source)
    assert source.count("60c6facf843685802c39e4adff4a05c081c1c4b6175c9cb573745c55abb0fa6a") == 1
    assert "manually editing the case JSON" in source
    ethics = section(source, "9. Ethics and data governance", "10. Conclusions")
    ethics_paragraph = ethics.split("\n\n")[0]
    readme = (PAPER.parent / "README.md").read_text()
    assert " ".join(ethics_paragraph.split()) in " ".join(readme.split())
    for index in range(1, 10):
        assert f"| C{index} " in source
    highlights = (PAPER / "HIGHLIGHTS.md").read_text().splitlines()
    for line in highlights:
        if line.startswith("- "):
            assert len(line[2:]) <= 85, f"Highlight exceeds 85 characters: {line}"
    print("Manuscript consistency checks passed.")


if __name__ == "__main__":
    check()
