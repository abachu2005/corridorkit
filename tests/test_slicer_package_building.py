"""Exact Slicer package builder/verifier contracts."""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "packaging" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wheelhouse(tmp_path):
    directory = tmp_path / "wheels"
    directory.mkdir()
    (directory / "corridorkit-0.1.0-py3-none-any.whl").write_bytes(b"project-wheel")
    (directory / "numpy-1.0-py3-none-any.whl").write_bytes(b"dependency-wheel")
    return directory


def test_builder_is_byte_reproducible_and_verifier_accepts_it(tmp_path, wheelhouse):
    builder = load_script("build_slicer_package.py")
    verifier = load_script("verify_slicer_package.py")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    first_result = builder.build(first, wheelhouse)
    second_result = builder.build(second, wheelhouse)

    assert first.read_bytes() == second.read_bytes()
    assert first_result["sha256"] == second_result["sha256"]
    result = verifier.verify(first)
    assert result["verified"] is True
    assert result["additional_module_path"].endswith("slicer/SkullBaseComparison")

    with zipfile.ZipFile(first) as archive:
        manifest = json.loads(archive.read("CorridorKit/package-manifest.json"))
        assert set(
            name.removeprefix("slicer/SkullBaseComparison/")
            for name in manifest["files"]
            if name.startswith("slicer/SkullBaseComparison/")
        ) == verifier.REQUIRED_MODULE_FILES
        assert manifest["files"]["slicer/runtime/bin/python"]["executable"] is True


def test_verifier_rejects_undeclared_file(tmp_path, wheelhouse):
    builder = load_script("build_slicer_package.py")
    verifier = load_script("verify_slicer_package.py")
    original = tmp_path / "original.zip"
    tampered = tmp_path / "tampered.zip"
    builder.build(original, wheelhouse)

    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as destination:
        for item in source.infolist():
            destination.writestr(item, source.read(item.filename))
        destination.writestr("CorridorKit/secrets/token.txt", "credential")

    with pytest.raises(ValueError, match="forbidden"):
        verifier.verify(tampered)


def test_verifier_rejects_hash_mismatch(tmp_path, wheelhouse):
    builder = load_script("build_slicer_package.py")
    verifier = load_script("verify_slicer_package.py")
    original = tmp_path / "original.zip"
    tampered = tmp_path / "tampered.zip"
    builder.build(original, wheelhouse)

    target = "CorridorKit/slicer/SkullBaseComparison/IntegratedPlanning.py"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as destination:
        for item in source.infolist():
            content = b"tampered\n" if item.filename == target else source.read(item.filename)
            destination.writestr(item, content)

    with pytest.raises(ValueError, match="size/hash mismatch"):
        verifier.verify(tampered)
