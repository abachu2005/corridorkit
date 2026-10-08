"""Packaging preflight contracts without launching PyInstaller."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def builder(monkeypatch):
    packaging = Path(__file__).resolve().parents[1] / "packaging"
    monkeypatch.syspath_prepend(str(packaging))
    spec = importlib.util.spec_from_file_location("corridor_macos_builder", packaging / "build_macos.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_rejects_wrong_platform(builder, monkeypatch):
    monkeypatch.setattr(builder.platform, "system", lambda: "Linux")
    with pytest.raises(SystemExit, match="macOS only"):
        builder.main()


def test_build_preserves_disk_reserve(builder, monkeypatch):
    monkeypatch.setattr(builder.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(builder.shutil, "disk_usage", lambda _: SimpleNamespace(free=1024**3))
    monkeypatch.setattr(builder.subprocess, "run", lambda *a, **k: pytest.fail("Build must not run"))
    with pytest.raises(SystemExit, match="at least 5 GiB"):
        builder.main()
