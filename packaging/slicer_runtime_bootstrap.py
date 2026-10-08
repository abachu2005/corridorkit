"""Verify and provision the packaged CorridorKit runtime on demand."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path


RUNTIME = Path(__file__).resolve().parent
MANIFEST_PATH = RUNTIME / "runtime-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise RuntimeError("Unsupported managed-runtime manifest")
    for item in manifest.get("wheels", []):
        path = (RUNTIME / item["path"]).resolve()
        if path.parent != (RUNTIME / "wheels").resolve():
            raise RuntimeError(f"Invalid wheel path: {item['path']}")
        if not path.is_file() or path.stat().st_size != item["size"]:
            raise RuntimeError(f"Missing or truncated managed-runtime wheel: {path.name}")
        if sha256(path) != item["sha256"]:
            raise RuntimeError(f"Managed-runtime wheel hash mismatch: {path.name}")
    return manifest


def cache_root() -> Path:
    configured = os.environ.get("CORRIDORKIT_RUNTIME_CACHE")
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "CorridorKit"


def runtime_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def provision(manifest: dict) -> Path:
    identity = hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
    destination = cache_root() / identity
    python = runtime_python(destination)
    ready = destination / ".complete"
    if ready.is_file() and python.is_file():
        return python

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{identity}.", dir=destination.parent) as temporary:
        candidate = Path(temporary) / "runtime"
        venv.EnvBuilder(with_pip=True, clear=True).create(candidate)
        candidate_python = runtime_python(candidate)
        wheels = [str(RUNTIME / item["path"]) for item in manifest["wheels"]]
        subprocess.run(
            [
                str(candidate_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                *wheels,
                *manifest["third_party_requirements"],
            ],
            check=True,
        )
        imports = "; ".join(f"import {name}" for name in manifest["import_check"])
        subprocess.run([str(candidate_python), "-c", imports], check=True)
        (candidate / ".complete").write_text(identity + "\n", encoding="utf-8")
        try:
            candidate.replace(destination)
        except FileExistsError:
            # Another process completed the same immutable runtime first.
            pass
    if not ready.is_file() or not python.is_file():
        raise RuntimeError("Managed runtime provisioning did not complete")
    return python


def main() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit("CorridorKit managed runtime requires Python 3.11 or newer")
    manifest = load_manifest()
    python = provision(manifest)
    completed = subprocess.run([str(python), *sys.argv[1:]])
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
