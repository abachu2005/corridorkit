"""Contracts for lightweight release checks; never build binaries or recurse into pytest."""
import importlib.util
import io
from pathlib import Path
import subprocess
import tarfile
from types import SimpleNamespace
import zipfile

import pytest


@pytest.fixture
def verifier():
    path = Path(__file__).resolve().parents[1] / "packaging/verify_source_release.py"
    spec = importlib.util.spec_from_file_location("source_verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_groups_partition_every_test_and_isolate_mixed_modules(verifier, tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    for name, text in {
        "test_math.py": "import numpy",
        "test_gui.py": "from PySide6 import QtWidgets",
        "test_mixed.py": "from skullbase_corridor.desktop.comparison import ComparisonPanel",
    }.items():
        (tests / name).write_text(text)
    groups = verifier.test_groups(tmp_path)
    assert [name for name, _ in groups] == [
        "numerical", "isolated:test_gui", "isolated:test_mixed",
    ]
    assert len({path for _, paths in groups for path in paths}) == 3
    assert all(len(paths) == 1 for _, paths in groups)


def test_real_groups_cover_all_tests_once(verifier):
    groups = verifier.test_groups(verifier.ROOT)
    paths = [path for _, group in groups for path in group]
    assert len(paths) == len(set(paths))
    assert set(paths) == set((verifier.ROOT / "tests").rglob("test_*.py"))
    numerical = next(paths for name, paths in groups if name == "numerical")
    assert not any(path.stem == "test_coverage_comparison" for path in numerical)


def test_environment_replaces_source_path_and_forces_offscreen(verifier, monkeypatch, tmp_path):
    monkeypatch.setenv("PYTHONPATH", "/unrelated/checkout")
    monkeypatch.setenv("QT_QPA_PLATFORM", "cocoa")
    env = verifier.source_environment(tmp_path)
    assert env["PYTHONPATH"] == str(tmp_path / "src")
    assert env["QT_QPA_PLATFORM"] == "offscreen"
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_source_allowlist_excludes_research_data_and_includes_oracle(verifier):
    names = {path.relative_to(verifier.ROOT).as_posix()
             for path in verifier.release_files(verifier.ROOT)}
    assert "packaging/verify_source_release.py" in names
    assert "tests/test_source_release.py" in names
    assert "research/run_voxel_refinement.py" in names
    assert {name for name in names if name.startswith("research/")} == {
        "research/run_voxel_refinement.py",
        "research/prepare_interactive_real_case.py",
        "research/interactive_real_smoke.py",
        "research/prepare_skullbase_atlas.py",
        "research/run_slicer_bridge.py",
        "research/benchmark_entry_pipeline.py",
        "research/run_real_ct_entry_acceptance.py",
    }
    assert "slicer/SkullBaseComparison/SkullBaseComparison.py" in names
    assert "slicer/SkullBaseComparison/CorridorAnalysis.py" in names
    assert not any(name.endswith((".npy", ".nii", ".gz", ".png")) for name in names)


def test_run_check_reports_timeout_and_failure(verifier, monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(verifier.subprocess, "run", timeout)
    assert not verifier.run_check("timeout", ["fake"], tmp_path, {}, 1)["passed"]
    monkeypatch.setattr(verifier.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=3, stdout="failure"))
    result = verifier.run_check("failed", ["fake"], tmp_path, {}, 1)
    assert not result["passed"] and result["returncode"] == 3


def test_build_preserves_prior_artifacts(verifier, tmp_path):
    (tmp_path / "old.whl").write_text("preserve")
    with pytest.raises(ValueError, match="new or empty"):
        verifier.build_release(verifier.ROOT, tmp_path, 1)
    assert (tmp_path / "old.whl").read_text() == "preserve"


def test_build_disk_guard(verifier, monkeypatch, tmp_path):
    monkeypatch.setattr(verifier.shutil, "disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match="512 MiB"):
        verifier.build_release(verifier.ROOT, tmp_path / "new", 1)


def test_report_overwrite_rejected(verifier, tmp_path):
    report = tmp_path / "evidence.json"
    report.write_text("preserve")
    with pytest.raises(SystemExit):
        verifier.main(["--report", str(report)])
    assert report.read_text() == "preserve"


def test_archive_verifier_requires_exact_source(verifier, tmp_path):
    root, output = tmp_path / "source", tmp_path / "output"
    code = root / "src/skullbase_corridor/__init__.py"
    code.parent.mkdir(parents=True)
    code.write_text('__version__ = "0.1.0"\n')
    output.mkdir()
    wheel = output / "skullbase_corridor-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("skullbase_corridor/__init__.py", code.read_bytes())
        archive.writestr("skullbase_corridor-0.1.0.dist-info/METADATA", "Name: skullbase-corridor\nVersion: 0.1.0\n")
    with tarfile.open(output / "skullbase_corridor-0.1.0.tar.gz", "w:gz") as archive:
        for name, content in {
            "src/skullbase_corridor/__init__.py": code.read_bytes(), "PKG-INFO": b"metadata",
        }.items():
            item = tarfile.TarInfo("skullbase_corridor-0.1.0/" + name)
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
    assert len(verifier.verify_archives(output, root, [code], "0.1.0")) == 2
    code.write_text("changed source")
    with pytest.raises(ValueError, match="Wheel code differs"):
        verifier.verify_archives(output, root, [code], "0.1.0")


def test_main_propagates_failed_checks(verifier, monkeypatch):
    monkeypatch.setattr(verifier, "run_check", lambda *a, **k: {"passed": False})
    assert verifier.main([]) == 1


def test_source_changes_during_checks_fail(verifier, monkeypatch):
    monkeypatch.setattr(verifier, "run_check", lambda *a, **k: {"passed": True})
    calls = 0
    count = len(verifier.release_files(verifier.ROOT))

    def changing_hash(path):
        nonlocal calls
        calls += 1
        return "before" if calls <= count else "after"

    monkeypatch.setattr(verifier, "sha256", changing_hash)
    assert verifier.main([]) == 1
