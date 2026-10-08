"""Build a deterministic, checkout-independent 3D Slicer extension archive.

The archive contains the scripted module and the exact project wheel. A small
launcher provisions a hash-identified user runtime on first use, without
changing Slicer's embedded Python environment. Third-party ML dependencies are
installed from the configured Python package index during explicit setup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
MODULE_SOURCE = ROOT / "slicer" / "SkullBaseComparison"
MODULE_FILES = (
    "SkullBaseComparison.py",
    "CorridorAnalysis.py",
    "EntryProposals.py",
    "IntegratedPlanning.py",
)
MODULE_RESOURCES = ("Resources/Icons/SkullBaseComparison.png",)
SOURCE_DATE_EPOCH = 315532800  # 1980-01-01, the first date representable by ZIP.
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
ARCHIVE_ROOT = "CorridorKit"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def build_wheelhouse(destination: Path) -> None:
    """Build only the portable project wheel; resolve third parties at setup."""
    environment = os.environ.copy()
    environment.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "SOURCE_DATE_EPOCH": str(SOURCE_DATE_EPOCH),
        }
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-cache-dir",
            "--wheel-dir",
            str(destination),
            str(ROOT),
        ],
        check=True,
        env=environment,
    )


def validate_wheelhouse(wheelhouse: Path) -> list[Path]:
    wheels = sorted(wheelhouse.glob("*.whl"), key=lambda path: path.name.lower())
    if not wheels:
        raise ValueError(f"wheelhouse has no wheels: {wheelhouse}")
    if not any(path.name.lower().startswith("corridorkit-") for path in wheels):
        raise ValueError("wheelhouse is missing the corridorkit project wheel")
    names = [path.name.lower() for path in wheels]
    if len(names) != len(set(names)):
        raise ValueError("wheelhouse contains case-insensitive duplicate names")
    return wheels


def stage_payload(stage: Path, wheelhouse: Path) -> dict[str, object]:
    package = stage / ARCHIVE_ROOT
    module = package / "slicer" / "SkullBaseComparison"
    runtime = package / "slicer" / "runtime"
    wheels_destination = runtime / "wheels"
    module.mkdir(parents=True)
    wheels_destination.mkdir(parents=True)

    for name in MODULE_FILES:
        shutil.copyfile(MODULE_SOURCE / name, module / name)
    for name in MODULE_RESOURCES:
        destination = module / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(MODULE_SOURCE / name, destination)

    # SkullBaseComparison loads this small numerical core directly for its
    # in-process landmark measurements.
    anatomical = ROOT / "src" / "corridorkit" / "analysis" / "anatomical.py"
    core_destination = package / "src" / "corridorkit" / "analysis"
    core_destination.mkdir(parents=True)
    shutil.copyfile(anatomical, core_destination / anatomical.name)

    copied_wheels = []
    for wheel in validate_wheelhouse(wheelhouse):
        destination = wheels_destination / wheel.name
        shutil.copyfile(wheel, destination)
        copied_wheels.append(destination)

    shutil.copyfile(Path(__file__).with_name("slicer_runtime_bootstrap.py"), runtime / "bootstrap.py")
    launcher = runtime / "bin" / "python"
    launcher.parent.mkdir()
    launcher.write_text(
        '#!/bin/sh\nexec "${CORRIDORKIT_BOOTSTRAP_PYTHON:-python3}" '
        '"$(dirname "$0")/../bootstrap.py" "$@"\n',
        encoding="utf-8",
        newline="\n",
    )
    launcher.chmod(0o755)

    runtime_manifest = {
        "schema_version": 1,
        "python_requires": ">=3.11",
        "install_mode": "online-third-party-setup",
        "third_party_requirements": [
            "TotalSegmentator==2.11.0",
        ],
        "setup_requires_network": True,
        "model_weights_bundled": False,
        "import_check": [
            "corridorkit",
            "totalsegmentator",
            "numpy",
            "scipy",
            "nibabel",
            "SimpleITK",
            "pydantic",
        ],
        "wheels": [
            {
                "path": f"wheels/{wheel.name}",
                "sha256": sha256_file(wheel),
                "size": wheel.stat().st_size,
            }
            for wheel in copied_wheels
        ],
    }
    (runtime / "runtime-manifest.json").write_bytes(canonical_json(runtime_manifest))

    payload_files = sorted(
        path for path in package.rglob("*") if path.is_file() and path.name != "package-manifest.json"
    )
    files = {}
    for path in payload_files:
        relative = path.relative_to(package).as_posix()
        files[relative] = {
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
            "executable": relative == "slicer/runtime/bin/python",
        }
    manifest = {
        "schema_version": 1,
        "product": "CorridorKit",
        "version": project_version(),
        "slicer_compatibility": "5.12",
        "additional_module_path": "CorridorKit/slicer/SkullBaseComparison",
        "runtime_entrypoint": "CorridorKit/slicer/runtime/bin/python",
        "files": files,
    }
    (package / "package-manifest.json").write_bytes(canonical_json(manifest))
    return manifest


def write_deterministic_zip(stage: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)
    paths = sorted(
        (path for path in stage.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(stage).as_posix(),
    )
    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, strict_timestamps=True
    ) as archive:
        for path in paths:
            relative = path.relative_to(stage).as_posix()
            mode = 0o755 if relative.endswith("/runtime/bin/python") else 0o644
            info = zipfile.ZipInfo(relative, ZIP_TIME)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.flag_bits = 0x800
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    temporary.replace(output)


def build(output: Path, wheelhouse: Path | None = None) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="corridorkit-slicer-package-") as temporary:
        workspace = Path(temporary)
        if wheelhouse is None:
            wheelhouse = workspace / "wheelhouse"
            wheelhouse.mkdir()
            build_wheelhouse(wheelhouse)
        manifest = stage_payload(workspace / "stage", wheelhouse.resolve())
        write_deterministic_zip(workspace / "stage", output)
    return {
        "archive": str(output),
        "sha256": sha256_file(output),
        "size": output.stat().st_size,
        "additional_module_path": manifest["additional_module_path"],
        "files": len(manifest["files"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / f"CorridorKit-Slicer-5.12-{project_version()}.zip",
    )
    parser.add_argument(
        "--wheelhouse",
        type=Path,
        help="Use an existing wheel directory containing the project wheel.",
    )
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    result = build(output, args.wheelhouse)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
