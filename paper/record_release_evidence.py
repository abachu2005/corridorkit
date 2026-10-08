"""Publish aggregate evidence from verify_release.py without local paths/logs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    report = json.loads((run / "release-verification.json").read_text())
    assert report["version"] == "0.3.0"
    assert report["source_and_tests_passed"] and report["wheel_passed"]
    assert report["native_slicer"]["slicer_integration_passed"]
    assert report["native_desktop"]["passed"] and report["source_unchanged"]
    source = json.loads((run / "source-verification.json").read_text())
    report["test_groups"] = []
    for check in source["checks"]:
        if check["name"] == "numerical" or check["name"].startswith("isolated:"):
            summaries = re.findall(r"(\d+ passed[^\n]*)", check.get("output", ""))
            report["test_groups"].append({
                "name": check["name"], "passed": check["passed"],
                "summary": summaries[-1] if summaries else "See source verification log",
            })
    report["dependencies"] = source["dependencies"]
    report["source_sha256"] = source["source_sha256"]
    report["artifacts"] = [
        {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
         "bytes": path.stat().st_size}
        for path in sorted((run / "artifacts").iterdir()) if path.is_file()
    ]
    destination = ROOT / "docs/evidence/corridorkit-v0.3.0-verification.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    for name in ("planning-workspace.png", "eea-path.png", "transmaxillary-path.png",
                 "coverage-comparison.png", "report.json"):
        shutil.copyfile(run / "worked-example" / name, ROOT / "paper/figures/worked-example" / name)
    shutil.copyfile(run / "quantitative-summary.json",
                    ROOT / "paper/figures/worked-example/quantitative-summary.json")
    print(destination)


if __name__ == "__main__":
    main()
