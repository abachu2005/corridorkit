"""Check the public Zenodo record and local citation against the source release."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, type=int)
    args = parser.parse_args()
    with urllib.request.urlopen(f"https://zenodo.org/api/records/{args.record}", timeout=60) as response:
        record = json.load(response)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    metadata = json.loads((ROOT / ".zenodo.json").read_text())
    citation = (ROOT / "CITATION.cff").read_text()
    manuscript = (ROOT / "paper/softwarex.md").read_text()
    assert project["name"] == "corridorkit"
    assert record["metadata"]["version"] == metadata["version"] == project["version"] == "0.3.0"
    assert record["metadata"]["title"] == metadata["title"]
    assert record["metadata"]["title"].startswith("CorridorKit:")
    assert record["doi"] in citation and record["doi"] in manuscript
    assert len(record["metadata"]["creators"]) == 3
    assert record.get("submitted") and record["status"] == "published"
    files = record["files"]
    assert files and any("corridorkit" in item["key"].lower() for item in files)
    commit = subprocess.check_output(
        ["git", "rev-parse", "v0.3.0^{commit}"], cwd=ROOT, text=True).strip()
    assert commit in manuscript
    print(json.dumps({
        "passed": True, "software": "CorridorKit", "version": project["version"],
        "doi": record["doi"], "concept_doi": record.get("conceptdoi"),
        "git_commit": commit, "archive_files": [item["key"] for item in files],
        "creators": [item["name"] for item in record["metadata"]["creators"]],
    }, indent=2))


if __name__ == "__main__":
    main()
