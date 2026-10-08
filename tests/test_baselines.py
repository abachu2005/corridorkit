import math

import numpy as np
import pytest

from skullbase_corridor.analysis.baselines import (
    analytic_cap_solid_angle,
    off_axis_target_solid_angle,
    optimized_capsule_sphere_clearance,
    parameter_hash,
    run_engine_ablations,
)
from skullbase_corridor.domain.models import CorridorCase
from skullbase_corridor.geometry.primitives import capsule_sphere_clearance


def small_case():
    approach = {
        "name": "A", "kind": "eea",
        "portal": {"center_mm": [0, 0, 0], "normal": [0, 0, 1], "radius_mm": 2},
        "nominal_direction": [0, 0, 1],
        "instrument": {"length_mm": 20, "radius_mm": .5},
        "sampling": {"polar_steps": 3, "azimuth_steps": 8, "max_angle_deg": 25},
        "target_tolerance_mm": .01,
    }
    return CorridorCase.model_validate({
        "case_id": "side-obstacle",
        "target": {"points_mm": [[2, 0, 10]]},
        "approaches": [approach],
        "protected_structures": [{"name": "synthetic-sphere", "geometry": {
            "kind": "sphere", "center_mm": [1.5, 0, 5], "radius_mm": .2}}],
    })


def test_actual_engine_ablation_uses_identical_target_directed_samples():
    case = small_case()
    rows = run_engine_ablations(case)
    assert [r["model"] for r in rows] == ["base_ray_cone", "finite_shaft", "simultaneous"]
    assert all(r["error"] is None for r in rows)
    results = [r["result"]["approaches"][0] for r in rows]
    assert results[0]["status"] == "complete"
    assert results[1]["status"] == "no_feasible_trajectory"
    samples = [[(t["direction"], t["entry_point_mm"], t["sampling_source"])
                for t in result["trajectories"]] for result in results]
    assert samples[0] == samples[1] == samples[2]
    assert any(t["sampling_source"] == "target_directed" for t in results[0]["trajectories"])
    assert case.approaches[0].instrument.radius_mm == .5
    assert rows[1]["parameters"]["case"] == rows[2]["parameters"]["case"]
    assert all(row["runtime_seconds"] >= 0 and len(row["parameter_hash"]) == 64 for row in rows)


def test_shared_and_disjoint_portals_keep_pair_outcomes():
    for separated in (False, True):
        data = small_case().model_dump(mode="json")
        data["protected_structures"] = []
        data["target"]["points_mm"] = [[-5, 0, 10], [5, 0, 10]] if separated else [
            [-2, 0, 10], [2, 0, 10]]
        first = data["approaches"][0]
        import copy
        second = copy.deepcopy(first)
        second["name"] = "B"
        if separated:
            first["portal"]["center_mm"] = [-3, 0, 0]
            second["portal"]["center_mm"] = [3, 0, 0]
        data["approaches"] = [first, second]
        rows = run_engine_ablations(CorridorCase.model_validate(data))
        assert all(row["statuses"]["approaches"] == ["complete", "complete"] for row in rows)
        assert rows[2]["result"]["simultaneous_pairs"][0]["feasible"] is separated


def test_pure_off_axis_quadrature_against_analytic_cap():
    from skullbase_corridor.geometry.engine import analyze_case

    data = small_case().model_dump(mode="json")
    data["protected_structures"] = []
    theta, phi = math.radians(19), math.radians(71)
    data["target"]["points_mm"] = [[10 * math.sin(theta) * math.cos(phi),
                                   10 * math.sin(theta) * math.sin(phi), 10 * math.cos(theta)]]
    approach = data["approaches"][0]
    approach["instrument"]["radius_mm"] = 0
    approach["target_tolerance_mm"] = 2
    approach["sampling"].update(max_angle_deg=45, target_directed=False,
                                adaptive_levels=0, polar_steps=17, azimuth_steps=96)
    result = analyze_case(CorridorCase.model_validate(data)).approaches[0]
    expected = off_axis_target_solid_angle(10, 2, 19, 45)
    assert abs(result.feasible_solid_angle_sr - expected) < .003
    assert all(t.sampling_source == "angular_grid" for t in result.trajectories)


def test_sphere_clearance_independent_optimizer_matches_analytic_primitive():
    rng = np.random.default_rng(1235)
    for _ in range(100):
        start, end, center = rng.normal(size=(3, 3)) * 10
        first, second = rng.uniform(0, 3, 2)
        expected = optimized_capsule_sphere_clearance(start, end, first, center, second)
        actual = capsule_sphere_clearance(start, end, first, center, second)
        assert actual == pytest.approx(expected, abs=1e-9)
    assert optimized_capsule_sphere_clearance([0, 0, 0], [0, 0, 0], .5,
                                               [2, 0, 0], 1) == pytest.approx(.5)
    assert optimized_capsule_sphere_clearance([0, 0, -2], [0, 0, 2], .5,
                                               [1.5, 0, 0], 1) == pytest.approx(0)


@pytest.mark.parametrize("invalid", [np.inf, np.nan, -1])
def test_reference_rejects_invalid_radius(invalid):
    with pytest.raises(ValueError):
        optimized_capsule_sphere_clearance([0, 0, 0], [1, 1, 1], invalid, [0, 0, 0], 1)


def test_analytic_cap_and_hash_contract():
    assert analytic_cap_solid_angle(90) == pytest.approx(2 * math.pi)
    assert off_axis_target_solid_angle(10, 5, 10, 50) == pytest.approx(
        2 * math.pi * (1 - math.sqrt(.75)))
    with pytest.raises(ValueError):
        off_axis_target_solid_angle(10, 5, 30, 50)
    with pytest.raises(ValueError):
        off_axis_target_solid_angle(10, 5, np.nan, 50)
    with pytest.raises(ValueError):
        analytic_cap_solid_angle(np.nan)
    assert parameter_hash({"b": 1, "a": 2}) == parameter_hash({"a": 2, "b": 1})
    with pytest.raises(ValueError):
        parameter_hash({"bad": np.nan})
