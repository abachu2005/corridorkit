"""Record final test/build evidence using commands, not hand-entered pass counts."""
from pathlib import Path
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from corridorkit.export.json import atomic_json_write, file_sha256


def main():
    destination = ROOT / "research/build-evidence"
    destination.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "QT_QPA_PLATFORM": "offscreen"}
    test = subprocess.run([sys.executable, "-m", "pytest", "-q",
                           f"--junitxml={destination / 'pytest.xml'}"],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    (destination / "pytest.log").write_text(test.stdout + test.stderr)
    suite = ET.parse(destination / "pytest.xml").getroot()
    totals = {key: sum(int(s.attrib.get(key, 0)) for s in suite.iter("testsuite"))
              for key in ("tests", "failures", "errors", "skipped")}
    totals["exit_code"] = test.returncode
    source = {str(path.relative_to(ROOT)): file_sha256(path)
              for path in sorted((ROOT / "src").rglob("*.py"))}
    report = {
        "python": platform.python_version(), "platform": platform.platform(),
        "dependencies": {name: importlib.metadata.version(name) for name in
                         ("numpy", "scipy", "SimpleITK", "nibabel", "pydantic", "PySide6", "vtk")},
        "tests": totals, "source_sha256": source,
        "distribution_files": {p.name: file_sha256(p) for p in (ROOT / "dist").glob("*")
                               if p.is_file()},
        "public_engine_result": "research/results/production-public-v3",
        "native_smoke": ["research/desktop-evidence/synthetic",
                         "research/desktop-evidence/public-development",
                         "research/desktop-evidence/public-computational"],
        "open_gates": ["expert_anatomy_review", "angular_measurement_accuracy",
                       "human_usability_acceptance", "native_bundle_disk_space"],
    }
    atomic_json_write(destination / "report.json", report)
    print(json.dumps(totals))
    if test.returncode:
        raise SystemExit(test.returncode)


if __name__ == "__main__":
    main()
