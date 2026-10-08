"""Contracts for the invented, explicitly nonclinical planning demonstration."""

import json
from time import perf_counter

import nibabel as nib
import numpy as np
import pytest

from corridorkit.analysis.coverage import build_coverage_comparison
from corridorkit.domain.models import AnalysisStatus, KnowledgeStatus, SampledMaskTarget
from corridorkit.export.json import file_sha256, read_case
from corridorkit.geometry.engine import analyze_case
from corridorkit.geometry.primitives import capsule_sphere_clearance
from corridorkit.io.masks import target_from_mask
from corridorkit.synthetic.planning import (
    CT_FILENAME,
    SYNTHETIC_WARNING,
    TARGET_FILENAME,
    write_planning_phantom,
)


@pytest.fixture
def phantom(tmp_path):
    path = write_planning_phantom(tmp_path / "demo")
    return path, read_case(path)


def test_full_mask_deterministic_geometry_and_volume(phantom, tmp_path):
    path, case = phantom
    second = write_planning_phantom(tmp_path / "replay")
    assert path.read_bytes() == second.read_bytes()
    assert case == read_case(second)
    for filename in (CT_FILENAME, TARGET_FILENAME):
        assert (path.parent / filename).read_bytes() == (second.parent / filename).read_bytes()
    assert sum(p.stat().st_size for p in path.parent.iterdir()) < 10_000_000

    ct = nib.load(path.parent / CT_FILENAME)
    mask = nib.load(path.parent / TARGET_FILENAME)
    assert ct.shape == mask.shape == (65, 73, 41)
    np.testing.assert_array_equal(ct.affine, mask.affine)
    assert nib.aff2axcodes(ct.affine) == ("R", "A", "S")
    assert ct.header.get_xyzt_units()[0] == mask.header.get_xyzt_units()[0] == "mm"
    assert np.min(ct.get_fdata()) < -500
    assert np.max(ct.get_fdata()) > 500
    assert set(np.unique(mask.get_fdata())) == {0, 1}

    target = case.target
    assert isinstance(target, SampledMaskTarget)
    assert target == target_from_mask(mask.get_fdata(), mask.affine)
    assert 100 <= len(target.points_mm) <= 500
    assert len(target.points_mm) == np.count_nonzero(mask.get_fdata())
    assert target.point_volume_mm3 == pytest.approx(8)
    assert target.voxel_volume_mm3 == pytest.approx(abs(np.linalg.det(mask.affine[:3, :3])))
    points = target.array()
    assert np.max(points[:, 1]) < 0  # Posterior target in RAS.
    for approach in case.approaches:
        assert approach.portal.center_mm[1] > np.max(points[:, 1])
        assert approach.nominal_direction[1] < 0
        assert approach.portal.radius_mm > approach.instrument.radius_mm > 0
        assert approach.instrument.length_mm < 150


def test_explicit_synthetic_safety_metadata_and_known_obstacles(phantom):
    path, case = phantom
    metadata = json.loads(path.read_text())["synthetic_metadata"]
    assert metadata["synthetic"] is True
    assert metadata["validated_anatomy"] is False
    assert metadata["clinical_use"] is False
    assert metadata["patient_data"] is False
    assert metadata["warning"] == SYNTHETIC_WARNING
    assert "NOT validated anatomy" in SYNTHETIC_WARNING
    assert "display-only" in SYNTHETIC_WARNING
    assert "SYNTHETIC" in case.case_id
    assert case.source_image.modality == "synthetic"
    assert case.source_image.sha256 == file_sha256(path.parent / case.source_image.uri)
    assert metadata["target_mask_uri"] == TARGET_FILENAME
    assert metadata["target_mask_sha256"] == file_sha256(path.parent / TARGET_FILENAME)
    for filename in (CT_FILENAME, TARGET_FILENAME):
        description = bytes(nib.load(path.parent / filename).header["descrip"])
        assert b"SYNTHETIC" in description
        assert b"NOT validated anatomy" in description
    assert len(case.protected_structures) >= 1
    for structure in case.protected_structures:
        assert "SYNTHETIC" in structure.name
        assert structure.status == KnowledgeStatus.KNOWN
        assert structure.geometry.kind == "sphere"
        assert not structure.allow_virtual_removal
    for approach in case.approaches:
        assert "SYNTHETIC" in approach.name
        assert not approach.allow_unknown_anatomy
        assert not approach.virtual_bone_removals
        assert approach.shared_portal_overlap_mm == 0
        assert approach.sampling.target_directed
        assert approach.sampling.max_target_witnesses <= 128


def test_both_approaches_feasible_and_tm_adds_volume(phantom):
    path, case = phantom
    start = perf_counter()
    result = analyze_case(case, base_directory=path.parent)
    elapsed = perf_counter() - start
    assert elapsed < 15, f"Small analytical fixture took {elapsed:.2f}s"
    assert result == analyze_case(case, base_directory=path.parent)
    eea, tm = result.approaches
    for config, approach in zip(case.approaches, result.approaches):
        assert approach.status == AnalysisStatus.COMPLETE
        assert approach.feasible_trajectory_count > 0
        assert not approach.sampling_complete
        assert approach.reached_measure_mm3 == pytest.approx(
            len(approach.reached_point_indices) * case.target.point_volume_mm3,
        )
        assert any(t.sampling_source == "target_directed" for t in approach.trajectories)
        for trajectory in approach.trajectories:
            assert not trajectory.conditional
            if not trajectory.feasible:
                continue
            entry = np.asarray(trajectory.entry_point_mm)
            direction = np.asarray(trajectory.direction)
            assert trajectory.minimum_clearance_mm > 0
            for depth in trajectory.insertion_depths_mm:
                assert 0 < depth <= config.instrument.length_mm
                for structure in case.protected_structures:
                    sphere = structure.geometry
                    assert capsule_sphere_clearance(
                        entry, entry + depth * direction, config.instrument.radius_mm,
                        np.asarray(sphere.center_mm), sphere.radius_mm,
                    ) > 0
    assert any(t.reason == "protected_structure_collision" for t in eea.trajectories)
    increment = set(tm.reached_point_indices) - set(eea.reached_point_indices)
    assert increment
    assert result.combinations[0].incremental_second_indices == sorted(increment)
    comparison = build_coverage_comparison(case, result)
    assert not comparison.issues
    assert comparison.tm_incremental_count == len(increment)
    assert comparison.tm_incremental_complete
    assert comparison.volume_mm3 is not None
    assert comparison.counts["both"] > 0
    assert comparison.tm_incremental_volume_mm3 == pytest.approx(len(increment) * 8)
    assert sum(comparison.volume_mm3.values()) == pytest.approx(
        len(case.target.points_mm) * 8,
    )
