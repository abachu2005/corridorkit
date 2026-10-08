"""Analytic synthetic geometry only: these are not clinical landmarks."""

import itertools
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from skullbase_corridor.analysis.anatomical import compare_landmarks


def landmarks():
    # XY approach angles to X: EEA=60 degrees, CTM=30 degrees.
    # Out-of-plane components make the 3-D distances deliberately different.
    return {
        "eea_entry": [-1, -np.sqrt(3), -2],
        "ctm_entry": [-np.sqrt(3), -1, -3],
        "target": [0, 0, 0],
        "ica_start": [-4, 2, 1],
        "ica_end": [4, 2, 5],
    }


def test_known_angles_and_full_3d_distances():
    result = compare_landmarks(**landmarks())
    assert result["eea_angle_deg"] == pytest.approx(60)
    assert result["ctm_angle_deg"] == pytest.approx(30)
    assert result["angle_advantage_deg"] == pytest.approx(30)
    assert result["eea_working_distance_mm"] == pytest.approx(np.sqrt(8))
    assert result["ctm_working_distance_mm"] == pytest.approx(np.sqrt(13))
    assert result["additional_lateral_reach_mm"] is None


@pytest.mark.parametrize("angle", [0, 15, 45, 90, 120, 180])
def test_acute_undirected_angle_including_boundaries(angle):
    radians = np.deg2rad(angle)
    result = compare_landmarks(
        [-np.cos(radians), -np.sin(radians), 0],
        [-1, 0, 0], [0, 0, 0], [0, 0, 0], [1, 0, 0],
    )
    assert result["eea_angle_deg"] == pytest.approx(min(angle, 180 - angle), abs=1e-12)
    assert 0 <= result["eea_angle_deg"] <= 90


def test_ica_endpoint_reversal():
    inputs = landmarks()
    original = compare_landmarks(**inputs)
    inputs["ica_start"], inputs["ica_end"] = inputs["ica_end"], inputs["ica_start"]
    reversed_result = compare_landmarks(**inputs)
    for key in ("eea_angle_deg", "ctm_angle_deg", "angle_advantage_deg"):
        assert reversed_result[key] == pytest.approx(original[key], abs=1e-12)


def test_exchanging_entries_reverses_advantage():
    inputs = landmarks()
    inputs["eea_entry"], inputs["ctm_entry"] = inputs["ctm_entry"], inputs["eea_entry"]
    assert compare_landmarks(**inputs)["angle_advantage_deg"] == pytest.approx(-30)


@pytest.mark.parametrize("seed", range(5))
def test_rigid_transforms_with_normal_and_signed_axis(seed):
    rng = np.random.default_rng(seed)
    rotation, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    if np.linalg.det(rotation) < 0:
        rotation[:, 0] *= -1
    translation = rng.normal(size=3) * 100
    inputs = landmarks()
    inputs.update(eea_limit=[1, 2, 4], ctm_limit=[4, 6, 10])
    normal = np.array([0, 0, 2.0])
    axis = np.array([-2, 1, 3.0])
    original = compare_landmarks(**inputs, plane_normal=normal, lateral_axis=axis)
    transformed = {key: rotation @ value + translation for key, value in inputs.items()}
    result = compare_landmarks(
        **transformed, plane_normal=rotation @ normal, lateral_axis=rotation @ axis
    )
    for key in (
        "eea_angle_deg", "ctm_angle_deg", "angle_advantage_deg",
        "eea_working_distance_mm", "ctm_working_distance_mm", "additional_lateral_reach_mm",
    ):
        assert result[key] == pytest.approx(original[key], abs=1e-10)


@pytest.mark.parametrize("normal", [(0, 0, -1), (0, 0, 1e-200), (0, 0, 1e200)])
def test_normal_sign_and_scale_do_not_change_measurements(normal):
    result = compare_landmarks(**landmarks(), plane_normal=normal)
    assert result["angle_advantage_deg"] == pytest.approx(30)


@pytest.mark.parametrize("axis, expected", [
    ([2, 0, 0], 3), ([-9, 0, 0], -3), ([0, 4, 0], 4), ([0, 0, -3], -6),
])
def test_signed_additional_lateral_reach(axis, expected):
    result = compare_landmarks(
        **landmarks(), eea_limit=[1, 2, 4], ctm_limit=[4, 6, 10], lateral_axis=axis
    )
    assert result["additional_lateral_reach_mm"] == pytest.approx(expected)


def test_identical_user_limits_are_valid_zero_reach():
    result = compare_landmarks(
        **landmarks(), eea_limit=[1, 2, 3], ctm_limit=[1, 2, 3], lateral_axis=[1, 0, 0]
    )
    assert result["additional_lateral_reach_mm"] == 0


@pytest.mark.parametrize("present", [
    flags for flags in itertools.product([False, True], repeat=3) if 0 < sum(flags) < 3
])
def test_reject_partial_lateral_inputs(present):
    names = ("eea_limit", "ctm_limit", "lateral_axis")
    optional = {name: [1, 0, 0] for name, supplied in zip(names, present) if supplied}
    with pytest.raises(ValueError, match="supplied together"):
        compare_landmarks(**landmarks(), **optional)


@pytest.mark.parametrize("name", [
    "eea_entry", "ctm_entry", "target", "ica_start", "ica_end", "plane_normal",
    "eea_limit", "ctm_limit", "lateral_axis",
])
@pytest.mark.parametrize("invalid", [
    None, "unknown", [1, 2], [1, 2, 3, 4], [[1, 2, 3]],
    [np.nan, 0, 0], [0, np.inf, 0], [0, 0, -np.inf],
    ["1", "2", "3"], [1 + 2j, 0, 0], [True, False, True],
])
def test_reject_unknown_or_invalid_triples(name, invalid):
    inputs = landmarks()
    inputs.update(eea_limit=[0, 0, 0], ctm_limit=[1, 0, 0], lateral_axis=[1, 0, 0])
    inputs[name] = invalid
    with pytest.raises(ValueError):
        compare_landmarks(**inputs)


@pytest.mark.parametrize("name, value", [
    ("plane_normal", [0, 0, 0]),
    ("eea_entry", [0, 0, 0]),
    ("ctm_entry", [0, 0, 0]),
    ("eea_entry", [0, 0, 3]),
    ("ctm_entry", [0, 0, -4]),
    ("ica_end", [-4, 2, 1]),
    ("ica_end", [-4, 2, 10]),
])
def test_reject_zero_or_collapsed_projected_vectors(name, value):
    inputs = landmarks()
    inputs[name] = value
    with pytest.raises(ValueError, match="nonzero|collapses"):
        compare_landmarks(**inputs)


def test_rotated_parallel_axis_is_rejected_despite_roundoff():
    normal = np.array([1, 2, 3.0])
    inputs = landmarks()
    inputs["ica_end"] = np.asarray(inputs["ica_start"]) + 3 * normal
    with pytest.raises(ValueError, match="collapses"):
        compare_landmarks(**inputs, plane_normal=normal)


def test_reject_zero_lateral_axis():
    with pytest.raises(ValueError, match="lateral_axis must be nonzero"):
        compare_landmarks(
            **landmarks(), eea_limit=[0, 0, 0], ctm_limit=[1, 0, 0], lateral_axis=[0, 0, 0]
        )


def test_unknown_keyword_is_not_silently_accepted():
    with pytest.raises(TypeError):
        compare_landmarks(**landmarks(), unknown_landmark=[1, 2, 3])


@pytest.mark.parametrize("updates", [
    {"eea_entry": [-1e308, 0, 0], "target": [1e308, 0, 0]},
    {"eea_entry": [-1.7e308, -1.7e308, 0]},
    {"ica_start": [-1e308, 0, 0], "ica_end": [1e308, 0, 0]},
    {"eea_limit": [-1e308, 0, 0], "ctm_limit": [1e308, 0, 0], "lateral_axis": [1, 0, 0]},
])
def test_unrepresentable_measurements_rejected_not_serialized_as_infinity(updates):
    inputs = landmarks()
    inputs.update(updates)
    with pytest.raises(ValueError, match="finite numeric range"):
        compare_landmarks(**inputs)


def test_json_safe_inputs_definitions_and_limitations():
    inputs = {key: np.asarray(value) for key, value in landmarks().items()}
    result = compare_landmarks(**inputs)
    assert json.loads(json.dumps(result, allow_nan=False)) == result
    assert result["coordinate_system"] == "world RAS"
    assert result["coordinate_units"] == "mm"
    for key, value in inputs.items():
        assert result["inputs"][key] == value.tolist()
    assert result["inputs"]["plane_normal"] == [0, 0, 1]
    assert result["inputs"]["lateral_axis"] is None
    assert "eea_angle_deg - ctm_angle_deg" in result["definitions"]["angle_advantage_deg"]
    limitations = " ".join(result["limitations"])
    assert "not a safe path" in limitations
    assert "user defined, not computed reachability" in limitations
    assert "3-D" in result["definitions"]["working_distances"]
    inputs["target"][0] = 99
    assert result["inputs"]["target"] == [0, 0, 0]


def test_standalone_module_does_not_import_pydantic_or_engine():
    # Test the owned file independently of legacy package __init__ imports.
    path = Path(__file__).parents[1] / "src/skullbase_corridor/analysis/anatomical.py"
    code = """
import importlib.util
import sys
class BlockHeavyImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'pydantic', 'scipy', 'skullbase_corridor'}:
            raise AssertionError('Unexpected heavy import: ' + fullname)
sys.meta_path.insert(0, BlockHeavyImports())
spec = importlib.util.spec_from_file_location('standalone_anatomical', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result = module.compare_landmarks([-1, -1, 0], [-1, 0, 0],
                                 [0, 0, 0], [0, 0, 0], [1, 0, 0])
assert abs(result['angle_advantage_deg'] - 45) < 1e-12
"""
    subprocess.run([sys.executable, "-c", code, str(path)], check=True)
