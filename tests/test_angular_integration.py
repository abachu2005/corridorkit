import math

import numpy as np
import pytest

from skullbase_corridor.analysis.angular_integration import integrate_solid_angle


@pytest.mark.parametrize("value", [False, True])
def test_constant_predicate_area_and_disclaimers(value):
    calls = []
    def feasible(direction):
        calls.append(direction)
        return value
    result = integrate_solid_angle(feasible, axis=(1, 2, 3), half_angle_deg=35)
    area = 2 * math.pi * (1 - math.cos(math.radians(35)))
    assert result.solid_angle_sr == pytest.approx(area if value else 0)
    assert result.sample_count == len(calls)
    assert result.sample_count == 5376  # 256*5 + 256*16, one uniform subdivision
    assert all(np.linalg.norm(v) == pytest.approx(1) for v in calls)
    assert result.estimated_error_sr == pytest.approx(0, abs=1e-12)
    assert result.error_is_certified is False
    assert result.rigorous_error_bound_sr is None
    assert result.termination_reason == "heuristic_tolerance"


def test_cap_boundary_refinement_and_sample_budget():
    theta, phi = math.radians(11), math.radians(23)
    target = 10 * np.array([math.sin(theta) * math.cos(phi),
                            math.sin(theta) * math.sin(phi), math.cos(theta)])
    def feasible(direction):
        axial = target @ direction
        return bool(axial >= 0 and np.linalg.norm(target - axial * direction) <= 2)
    result = integrate_solid_angle(feasible, max_samples=16000)
    expected = 2 * math.pi * (1 - math.sqrt(.96))
    assert abs(result.solid_angle_sr - expected) / expected < .02
    assert result.boundary_refinements > 0
    assert result.exploration_refinements > 0
    assert result.sample_count <= 16000
    assert result.termination_reason in ("sample_budget", "heuristic_tolerance")
    assert result.sampled_boundary_area_sr >= result.estimated_error_sr - 1e-12


def test_unseen_island_demonstrates_error_indicator_is_not_bound():
    cosine = math.cos(math.radians(.1))
    result = integrate_solid_angle(lambda d: bool(d[2] >= cosine))
    actual = 2 * math.pi * (1 - cosine)
    assert actual > 0
    assert result.solid_angle_sr == 0
    assert result.estimated_error_sr == 0
    assert result.sampled_boundary_area_sr == 0
    assert result.heuristic_tolerance_met
    assert not result.error_is_certified


def test_deterministic_nested_evaluations_and_domain():
    def run():
        points = []
        def predicate(direction):
            points.append(direction.tolist())
            return bool(direction[0] > .1)
        result = integrate_solid_angle(predicate, max_samples=6000)
        return result, points
    first, points = run()
    second, repeated = run()
    assert points == repeated
    assert first.solid_angle_sr == second.solid_angle_sr
    assert all(p[2] >= math.cos(math.radians(45)) - 1e-12 for p in points)
    assert first.sample_count <= 6000


@pytest.mark.parametrize("kwargs", [
    {"axis": [0, 0, 0]}, {"axis": [np.nan, 0, 1]}, {"half_angle_deg": np.inf},
    {"half_angle_deg": 181}, {"absolute_tolerance_sr": -1}, {"max_seconds": 0},
    {"max_samples": 100}, {"max_samples": 4000.5}, {"initial_u_cells": 0},
    {"minimum_uniform_depth": -1}, {"minimum_uniform_depth": 13},
    {"exploration_interval": 0},
])
def test_invalid_configuration_rejected(kwargs):
    with pytest.raises(ValueError):
        integrate_solid_angle(lambda d: True, **kwargs)


@pytest.mark.parametrize("answer", [None, 1, float("nan"), "complete"])
def test_unknown_or_nonboolean_callback_rejected(answer):
    with pytest.raises(ValueError, match="callback must return bool"):
        integrate_solid_angle(lambda d: answer)


def test_callback_error_not_silently_classified_as_infeasible():
    def broken(direction):
        raise RuntimeError("anatomy unknown")
    with pytest.raises(RuntimeError, match="anatomy unknown"):
        integrate_solid_angle(broken)
