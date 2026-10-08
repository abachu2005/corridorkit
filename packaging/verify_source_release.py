"""Verify current source; optionally test in isolated groups and build data-free archives.

No publishing, frozen application build, public-data download, or cache deletion.
Requires Python >=3.11; use the same interpreter as the installed dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
CORE_MODULES = (
    "domain.models", "geometry.engine", "geometry.voxel", "geometry.primitives",
    "analysis.coverage", "analysis.geometric_angular", "application.service",
    "application.session", "io.volumes", "io.masks", "io.registration",
    "export.json", "synthetic.cases", "synthetic.planning", "cli",
)
DESKTOP_MODULES = (
    "desktop.app", "desktop.views", "desktop.planning_overlays",
    "desktop.trajectory_geometry", "desktop.corridor_overlay", "desktop.comparison",
)


def source_environment(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return env


def test_groups(root: Path) -> list[tuple[str, list[Path]]]:
    """Separate every desktop/Qt-related module, including mixed numerical tests.

    Static detection deliberately errs toward isolation. conftest imports Qt but
    does not create QApplication; it is not a test module.
    """
    numerical, gui = [], []
    for path in sorted((root / "tests").rglob("test_*.py")):
        text = path.read_text()
        markers = ("PySide6", "pyqtgraph", "corridorkit.desktop", "QApplication")
        (gui if any(marker in text for marker in markers) else numerical).append(path)
    groups = [("numerical", numerical)] if numerical else []
    return groups + [(f"isolated:{path.stem}", [path]) for path in gui]


def release_files(root: Path) -> list[Path]:
    """Explicit source-only allowlist; public images, records and caches stay local."""
    files = [
        root / name for name in (
            "pyproject.toml", "README.md", "LICENSE", "CMakeLists.txt",
            "CorridorKit.s4ext", "CITATION.cff", ".zenodo.json",
        )
    ]
    for directory, suffixes in (
        ("src", {".py"}), ("tests", {".py"}), ("docs", {".md", ".json"}),
        ("packaging", {".py", ".md", ".command"}), ("slicer", {".py", ".md"}),
        ("paper", {".md", ".bib"}), ("infra", {".py", ".md", ".sh", ".bicep", ".Dockerfile"}),
    ):
        files.extend(
            path for path in (root / directory).rglob("*")
            if path.is_file() and path.suffix in suffixes
            and "__pycache__" not in path.parts
        )
    # Independent oracle tests import this source module, not cohort data.
    files.extend(root / "research" / name for name in (
        "run_voxel_refinement.py", "prepare_interactive_real_case.py", "interactive_real_smoke.py",
        "prepare_skullbase_atlas.py", "run_slicer_bridge.py",
        "benchmark_entry_pipeline.py",
        "run_real_ct_entry_acceptance.py",
    ))
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing or symlinked release input: {path}")
    return sorted(set(files))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_check(name, command, root, env, timeout):
    try:
        result = subprocess.run(
            command, cwd=root, env=env, timeout=timeout, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        record = {"name": name, "command": command, "returncode": result.returncode,
                  "passed": result.returncode == 0, "output": result.stdout}
    except (subprocess.TimeoutExpired, OSError) as error:
        record = {"name": name, "command": command, "passed": False, "error": str(error)}
    print(f"{'PASS' if record['passed'] else 'FAIL'}: {name}", flush=True)
    if record.get("output"):
        print(record["output"], flush=True)
    return record


def verify_archives(output: Path, root: Path, files: list[Path], version: str):
    """Require byte-for-byte current code and reject unexpected archive content."""
    wheel_name = f"corridorkit-{version}-py3-none-any.whl"
    sdist_name = f"corridorkit-{version}.tar.gz"
    source = {p.relative_to(root).as_posix(): p.read_bytes() for p in files}
    with zipfile.ZipFile(output / wheel_name) as archive:
        wheel = {name: archive.read(name) for name in archive.namelist()
                 if not name.endswith("/")}
    expected_code = {name[4:]: value for name, value in source.items()
                     if name.startswith("src/")}
    actual_code = {name: value for name, value in wheel.items()
                   if name.startswith("corridorkit/")}
    if actual_code != expected_code:
        raise ValueError("Wheel code differs from current source")
    metadata_prefix = f"corridorkit-{version}.dist-info/"
    if any(not (name in expected_code or name.startswith(metadata_prefix)) for name in wheel):
        raise ValueError("Unexpected wheel payload")
    metadata = wheel[metadata_prefix + "METADATA"].decode()
    if f"\nVersion: {version}\n" not in metadata:
        raise ValueError("Wheel version mismatch")
    with tarfile.open(output / sdist_name) as archive:
        members = archive.getmembers()
        if any(not (member.isfile() or member.isdir()) for member in members):
            raise ValueError("Unexpected sdist link or special file")
        prefix = f"corridorkit-{version}/"
        payload = {}
        for member in members:
            if member.isfile():
                if not member.name.startswith(prefix):
                    raise ValueError("Unexpected sdist root")
                payload[member.name[len(prefix):]] = archive.extractfile(member).read()
    if set(payload) != set(source) | {"PKG-INFO"}:
        raise ValueError("Sdist payload differs from source-only allowlist")
    if any(payload[name] != content for name, content in source.items()):
        raise ValueError("Sdist differs from current source")
    return [{"path": str(output / name), "sha256": sha256(output / name),
             "bytes": (output / name).stat().st_size}
            for name in (wheel_name, sdist_name)]


def build_release(root: Path, output: Path, timeout: float):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Build output must be new or empty; prior artifacts are preserved")
    if shutil.disk_usage(root).free < 512 * 1024**2:
        raise ValueError("Source build requires at least 512 MiB free; no cleanup is performed")
    files = release_files(root)
    before = {str(path): sha256(path) for path in files}
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="corridor-source-") as directory:
        staging = Path(directory)
        for path in files:
            destination = staging / path.relative_to(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        record = run_check(
            "source-only wheel and sdist",
            [sys.executable, "-m", "build", "--outdir", str(output)],
            staging, env, timeout,
        )
    if not record["passed"]:
        return record
    if before != {str(path): sha256(path) for path in release_files(root)}:
        raise ValueError("Source changed during build; artifacts are not verified")
    record["artifacts"] = verify_archives(output, root, files, version)
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests", action="store_true", help="Run all tests in fresh-process groups")
    parser.add_argument("--desktop", action="store_true", help="Require desktop imports (not native rendering)")
    parser.add_argument("--build", action="store_true", help="Build and inspect source-only wheel/sdist")
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "source-release")
    parser.add_argument("--report", type=Path, help="Write JSON evidence (must not already exist)")
    parser.add_argument("--timeout", type=float, default=300, help="Seconds per subprocess")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.report and args.report.exists():
        parser.error("--report already exists; use a fresh report path")
    root, env = ROOT.resolve(), source_environment(ROOT.resolve())
    initial_manifest = {str(p.relative_to(root)): sha256(p) for p in release_files(root)}
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    modules = CORE_MODULES + (DESKTOP_MODULES if args.desktop else ())
    code = (
        "import importlib, importlib.metadata, pathlib; "
        "import corridorkit as package; "
        f"assert package.__version__ == {project['version']!r}; "
        f"assert pathlib.Path(package.__file__).resolve() == pathlib.Path({str(root / 'src/corridorkit/__init__.py')!r}); "
        f"assert importlib.metadata.version('corridorkit') == {project['version']!r}; "
        "eps = {e.name: e.value for e in importlib.metadata.distribution('corridorkit').entry_points}; "
        "assert eps['corridorkit'] == 'corridorkit.cli:app'; "
        "assert eps['corridorkit-gui'] == 'corridorkit.desktop:main'; "
        f"[importlib.import_module('corridorkit.' + name) for name in {modules!r}]; "
        "print('source imports, installed version metadata and entry points agree')"
    )
    records = [run_check("imports and metadata", [sys.executable, "-c", code], root, env, args.timeout)]
    records.append(run_check("CLI help", [sys.executable, "-m", "corridorkit.cli", "--help"],
                             root, env, args.timeout))
    if args.tests:
        groups = test_groups(root)
        if not groups:
            records.append({"name": "test discovery", "passed": False, "error": "No tests found"})
        for name, paths in groups:
            records.append(run_check(name, [sys.executable, "-m", "pytest", "-q",
                                            *map(str, paths)], root, env, args.timeout))
    if args.build:
        try:
            records.append(build_release(root, args.output.resolve(), args.timeout))
        except (OSError, ValueError, KeyError, tarfile.TarError, zipfile.BadZipFile) as error:
            records.append({"name": "source build", "passed": False, "error": str(error)})
    final_manifest = {str(p.relative_to(root)): sha256(p) for p in release_files(root)}
    records.append({"name": "source unchanged throughout verification",
                    "passed": initial_manifest == final_manifest})
    dependencies = {}
    for name in ("numpy", "scipy", "nibabel", "SimpleITK", "pydicom", "pydantic",
                 "typer", "PySide6", "pyqtgraph", "vtk", "pytest", "build"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    report = {
        "passed": all(record["passed"] for record in records),
        "python": sys.version, "executable": sys.executable, "version": project["version"],
        "source_root": str(root), "free_bytes": shutil.disk_usage(root).free,
        "tests_requested": args.tests, "desktop_requested": args.desktop,
        "dependencies": dependencies,
        "initial_source_sha256": initial_manifest, "source_sha256": final_manifest,
        "checks": records,
        "limitations": [
            "Source imports use checkout; installed metadata alone is not installed-wheel verification.",
            "Test success may include skips; inspect each group's output.",
            "Offscreen GUI imports/tests do not verify native rendering or clinician acceptance.",
            "No clean-machine, cross-platform, frozen-bundle or clinical validation.",
        ],
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
    print(json.dumps({key: report[key] for key in ("passed", "version", "limitations")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
