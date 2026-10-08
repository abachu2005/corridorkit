"""Build a checkout-independent numerical runtime for the Slicer extension."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(command, **kwargs):
    subprocess.run([str(item) for item in command], check=True, **kwargs)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(destination: Path):
    if destination.exists():
        shutil.rmtree(destination)
    wheels = destination.parent / "runtime-wheels"
    shutil.rmtree(wheels, ignore_errors=True)
    wheels.mkdir(parents=True)
    run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", wheels, ROOT])
    wheel = next(wheels.glob("corridorkit-*.whl"))
    venv.EnvBuilder(with_pip=True, clear=True).create(destination)
    python = destination / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    run([
        python, "-m", "pip", "install",
        "--disable-pip-version-check", f"{wheel}[cloud]",
    ])
    check = (
        "import corridorkit, numpy, scipy, nibabel, SimpleITK, pydantic; "
        "from corridorkit.application.e2e import run_workflow"
    )
    run([python, "-c", check])
    manifest = {
        "schema_version": 1,
        "python": subprocess.check_output([python, "--version"], text=True).strip(),
        "wheel": wheel.name,
        "wheel_sha256": sha256(wheel),
        "runtime_python": str(python),
        "excludes_desktop_qt_vtk": True,
    }
    (destination / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--destination",
        type=Path,
        default=ROOT / "slicer/runtime",
    )
    args = parser.parse_args()
    print(json.dumps(build(args.destination.resolve()), indent=2))


if __name__ == "__main__":
    main()
