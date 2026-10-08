"""Install a built wheel in a temporary isolated package environment and smoke it.

System dependencies are reused intentionally; this does NOT certify a pristine
machine install or a native app bundle.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess
import tempfile
import venv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    with tempfile.TemporaryDirectory(prefix="corridor-wheel-") as directory:
        root = Path(directory)
        venv.create(root / "env", with_pip=True, system_site_packages=True)
        python = root / "env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        commands = [
            [str(python), "-m", "pip", "install", "--no-deps", "--force-reinstall", str(wheel)],
            [
                str(python), "-c",
                "import importlib.metadata, pathlib, sys, corridorkit; "
                "assert pathlib.Path(corridorkit.__file__).is_relative_to(pathlib.Path(sys.prefix)); "
                "assert importlib.metadata.version('corridorkit') == corridorkit.__version__; "
                "eps = {e.name: e.value for e in importlib.metadata.distribution('corridorkit').entry_points}; "
                "assert eps['corridorkit'] == 'corridorkit.cli:app'; "
                "assert eps['corridorkit-gui'] == 'corridorkit.desktop:main'",
            ],
            [str(python), "-m", "corridorkit.cli", "doctor"],
            [str(python), "-m", "corridorkit.cli", "synthetic", "case.json"],
            [str(python), "-m", "corridorkit.cli", "analyze", "case.json", "result.json"],
        ]
        for command in commands:
            subprocess.run(command, cwd=root, env=env, check=True)
        result = json.loads((root / "result.json").read_text())
        assert result["case_id"] == "synthetic-analytical-v1"
        assert len(result["result"]["approaches"]) == 2
        print("PASS: installed wheel CLI synthetic→analysis outside the source tree; shared dependencies")


if __name__ == "__main__":
    main()
