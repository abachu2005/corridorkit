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
            [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
            [str(python), "-m", "skullbase_corridor.cli", "doctor"],
            [str(python), "-m", "skullbase_corridor.cli", "synthetic", "case.json"],
            [str(python), "-m", "skullbase_corridor.cli", "analyze", "case.json", "result.json"],
        ]
        for command in commands:
            subprocess.run(command, cwd=root, env=env, check=True)
        result = json.loads((root / "result.json").read_text())
        assert result["case_id"] == "synthetic-analytical-v1"
        assert len(result["result"]["approaches"]) == 2
        print("PASS: installed wheel CLI synthetic→analysis outside the source tree; shared dependencies")


if __name__ == "__main__":
    main()
