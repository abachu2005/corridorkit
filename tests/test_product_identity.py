"""CorridorKit's breaking package rename must agree across runtime artifacts."""
import ast
from pathlib import Path
import tomllib

from typer.testing import CliRunner

import corridorkit
from corridorkit.analysis.statistics import subject_split
from corridorkit.cli import app
from corridorkit.export.json import software_version


ROOT = Path(__file__).resolve().parents[1]


def test_package_version_and_entry_points_agree():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = config["project"]
    assert project["name"] == "corridorkit"
    assert project["version"] == corridorkit.__version__ == "0.3.0"
    assert software_version() == "0.3.0"
    assert project["scripts"] == {"corridorkit": "corridorkit.cli:app"}
    assert project["gui-scripts"] == {"corridorkit-gui": "corridorkit.desktop:main"}
    assert config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/corridorkit"
    ]
    assert not (ROOT / "src" / "skullbase_corridor").exists()


def test_cli_displays_current_brand():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "CorridorKit" in result.stdout


def test_current_source_has_no_legacy_package_imports():
    for path in (ROOT / "src/corridorkit").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert not any(name.startswith("skullbase_corridor") for name in modules), path


def test_slicer_display_brand_and_extension_build_name_agree():
    source = (ROOT / "slicer/SkullBaseComparison/SkullBaseComparison.py").read_text()
    assert 'self.parent.title = "CorridorKit"' in source
    assert "project(CorridorKit)" in (ROOT / "CMakeLists.txt").read_text()
    assert (ROOT / "CorridorKit.s4ext").is_file()


def test_rename_preserves_historical_subject_assignments():
    subjects = [f"P{index:03}" for index in range(100)]
    assert subject_split(subjects) == subject_split(subjects, salt="skullbase-corridor-v1")
