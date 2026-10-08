"""Verify the exact contents and hashes of a CorridorKit Slicer package."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath


ROOT = "CorridorKit"
REQUIRED_MODULE_FILES = {
    "CorridorAnalysis.py",
    "EntryProposals.py",
    "IntegratedPlanning.py",
    "Resources/Icons/SkullBaseComparison.png",
    "SkullBaseComparison.py",
}
FORBIDDEN_PARTS = {
    ".env",
    ".git",
    "__pycache__",
    "cache",
    "data-cache",
    "credentials",
    "secrets",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ValueError(f"unsafe archive path: {name}")
    return path


def verify(archive_path: Path) -> dict[str, object]:
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("archive contains duplicate paths")
        for info in infos:
            path = safe_name(info.filename)
            lower_parts = {part.lower() for part in path.parts}
            if lower_parts & FORBIDDEN_PARTS:
                raise ValueError(f"forbidden cache/credential/data path: {info.filename}")
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"archive must contain regular files only: {info.filename}")
        if any(info.date_time != (1980, 1, 1, 0, 0, 0) for info in infos):
            raise ValueError("archive timestamps are not deterministic")
        if names != sorted(names):
            raise ValueError("archive entries are not sorted")

        manifest_name = f"{ROOT}/package-manifest.json"
        if manifest_name not in names:
            raise ValueError("package manifest is missing")
        manifest = json.loads(archive.read(manifest_name))
        if manifest.get("schema_version") != 1:
            raise ValueError("unsupported package manifest")
        if manifest.get("slicer_compatibility") != "5.12":
            raise ValueError("package does not declare Slicer 5.12 compatibility")
        if manifest.get("additional_module_path") != (
            "CorridorKit/slicer/SkullBaseComparison"
        ):
            raise ValueError("unexpected Additional module path")

        declared = manifest.get("files")
        if not isinstance(declared, dict):
            raise ValueError("manifest files must be an object")
        actual = set(names) - {manifest_name}
        expected = {f"{ROOT}/{name}" for name in declared}
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(f"archive/manifest mismatch; missing={missing}, extra={extra}")
        for relative, metadata in declared.items():
            data = archive.read(f"{ROOT}/{relative}")
            if len(data) != metadata["size"] or sha256(data) != metadata["sha256"]:
                raise ValueError(f"size/hash mismatch: {relative}")

        module_prefix = "slicer/SkullBaseComparison/"
        module_files = {
            path.removeprefix(module_prefix)
            for path in declared
            if path.startswith(module_prefix)
        }
        if module_files != REQUIRED_MODULE_FILES:
            raise ValueError(f"unexpected scripted module files: {sorted(module_files)}")

        runtime_manifest_path = "slicer/runtime/runtime-manifest.json"
        runtime_manifest = json.loads(
            archive.read(f"{ROOT}/{runtime_manifest_path}")
        )
        wheel_records = runtime_manifest.get("wheels", [])
        if not wheel_records:
            raise ValueError("runtime wheelhouse is empty")
        wheel_paths = {f"slicer/runtime/{item['path']}" for item in wheel_records}
        actual_wheels = {
            path for path in declared if path.startswith("slicer/runtime/wheels/")
        }
        if wheel_paths != actual_wheels:
            raise ValueError("runtime manifest does not exactly describe the wheelhouse")
        for item in wheel_records:
            relative = f"slicer/runtime/{item['path']}"
            metadata = declared[relative]
            if metadata["sha256"] != item["sha256"] or metadata["size"] != item["size"]:
                raise ValueError(f"runtime/package manifest disagreement: {relative}")
        if not any(
            PurePosixPath(item["path"]).name.lower().startswith("corridorkit-")
            for item in wheel_records
        ):
            raise ValueError("project wheel is missing")

        launcher = declared.get("slicer/runtime/bin/python")
        if not launcher or not launcher.get("executable"):
            raise ValueError("managed-runtime launcher is missing or not executable")

    return {
        "archive": str(archive_path),
        "sha256": sha256(archive_path.read_bytes()),
        "files": len(declared) + 1,
        "wheels": len(wheel_records),
        "additional_module_path": manifest["additional_module_path"],
        "verified": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    result = verify(args.archive.expanduser().resolve())
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
