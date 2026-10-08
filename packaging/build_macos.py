"""Build a local macOS research-alpha app, without signing or publishing."""
from __future__ import annotations

import platform
from pathlib import Path
import subprocess
import sys
import shutil
import os
import hashlib
import json
from importlib.metadata import version

from make_icon import make_icon

def main() -> None:
    if platform.system() != "Darwin":
        raise SystemExit("This entry point builds macOS only.")
    root = Path(__file__).resolve().parents[1]
    free = shutil.disk_usage(root).free
    if free < 5 * 1024**3:
        raise SystemExit(
            f"Native bundle build blocked: {free / 1024**3:.2f} GiB free; "
            "reserve at least 5 GiB for Qt/VTK staging. No files removed."
        )
    # Optional plotting/notebook backends in a broad Anaconda installation
    # must not pull a second Qt binding or an unrelated ML stack into the app.
    excludes = [
        "PyQt5", "PyQt6", "PySide2", "matplotlib", "IPython", "notebook",
        "pytest", "sphinx", "torch", "tensorflow", "pandas", "sklearn",
        "xarray", "dask", "pyarrow", "distributed", "vtkmodules.test",
        "vtkmodules.tk", "vtkmodules.gtk", "vtkmodules.wx",
        "vtkmodules.web", "vtkmodules.util.xarray_support",
    ]
    environment = os.environ.copy()
    environment["QT_API"] = "pyside6"
    icon = root / "packaging" / "corridor.icns"
    make_icon(icon)
    manifest = root / "build" / "build-manifest.json"
    manifest.parent.mkdir(exist_ok=True)
    manifest.write_text(json.dumps({
        "python": sys.version,
        "platform": platform.platform(),
        "packages": {name: version(name) for name in (
            "pyinstaller", "numpy", "scipy", "nibabel", "SimpleITK",
            "pydicom", "pydantic", "PySide6", "pyqtgraph", "vtk",
        )},
        "source_sha256": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ("src", "packaging")
            for path in sorted((root / folder).rglob("*.py"))
        },
    }, indent=2) + "\n")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed",
        "--icon", str(icon),
        "--add-data", f"{manifest}:.",
        "--name", "CorridorKit", "--paths", str(root / "src"),
        "--collect-all", "corridorkit", "--collect-submodules", "vtkmodules",
        "--hidden-import", "vtkmodules.qt.QVTKRenderWindowInteractor",
        *[arg for name in excludes for arg in ("--exclude-module", name)],
        str(root / "packaging" / "launcher.py"),
    ], cwd=root, check=True, env=environment)


if __name__ == "__main__":
    main()
