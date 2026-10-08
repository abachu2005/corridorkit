"""Independent analytic checks for production geometry-aware quadrature."""

import math

import numpy as np
import pytest

from corridorkit.analysis.geometric_angular import integrate_unobstructed_solid_angle
from corridorkit.domain.models import CorridorCase
from corridorkit.geometry.engine import analyze_case


def direction(tilt, azimuth):
    t, p = np.radians([tilt, azimuth])
    return np.array([math.sin(t) * math.cos(p), math.sin(t) * math.sin(p), math.cos(t)])


def integrate(targets, **kwargs):
    parameters = dict(axis=[0, 0, 1], half_angle_deg=45, portal_normal=[0, 0, 1],
                      portal_radius_mm=2, collision_radius_mm=0,
                      reach_radius_mm=2, instrument_length_mm=20,
                      geometric_tolerance_mm=0)
    parameters.update(kwargs)
    return integrate_unobstructed_solid_angle(targets, **parameters)


def cap_area(radius_deg):
    return 4 * math.pi * math.sin(math.radians(radius_deg) / 2) ** 2


def equal_cap_lens(radius_deg, separation_deg):
    """Gauss-Bonnet lens formula, independent of latitude integration."""
    r, d = np.radians([radius_deg, separation_deg])
    alpha = math.acos(math.cos(r) / math.sin(r) * math.tan(d / 2))
    gamma = math.acos((math.cos(d) - math.cos(r) ** 2) / math.sin(r) ** 2)
    return 2 * math.pi - 4 * alpha * math.cos(r) - 2 * gamma


@pytest.mark.parametrize("radius", [.1, 1., math.degrees(math.asin(.2)), 18.])
@pytest.mark.parametrize("tilt,azimuth", [(0, 0), (11, 23), (19, 71), (24, 179),
                                         (13, 359), (1e-4, 250)])
def test_rotated_caps_including_original_narrow_stress(radius, tilt, azimuth):
    answer = integrate([10 * direction(tilt, azimuth)],
                       reach_radius_mm=10 * math.sin(math.radians(radius)))
    assert answer.termination_reason == "estimated_tolerance"
    assert answer.solid_angle_sr == pytest.approx(cap_area(radius), rel=1e-4)
    assert answer.error_is_certified is False


@pytest.mark.parametrize("separation", [1., 12., 29.])
def test_overlapping_targets_union_does_not_double_count(separation):
    radius = 15.
    targets = [10 * direction(0, 0), 10 * direction(separation, 0)]
    result = integrate(targets, reach_radius_mm=10 * math.sin(math.radians(radius)))
    expected = 2 * cap_area(radius) - equal_cap_lens(radius, separation)
    assert result.solid_angle_sr == pytest.approx(expected, rel=1e-4)
    duplicate = integrate(targets + targets,
                          reach_radius_mm=10 * math.sin(math.radians(radius)))
    assert duplicate.solid_angle_sr == pytest.approx(result.solid_angle_sr, rel=1e-9)


@pytest.mark.parametrize("separation", [1., 20., 39.9])
def test_sampling_domain_clips_target_cap_including_narrow_lens(separation):
    radius = 20.
    answer = integrate([10 * direction(separation, 30)], half_angle_deg=radius,
                       reach_radius_mm=10 * math.sin(math.radians(radius)))
    expected = equal_cap_lens(radius, separation)
    assert answer.solid_angle_sr == pytest.approx(expected, rel=1e-4)


def test_finite_length_annulus_and_empty_band():
    distance, reach = 10., 2.
    lower = math.sqrt(distance ** 2 - reach ** 2)
    length = lower + .003
    answer = integrate([[0, 0, distance]], instrument_length_mm=length)
    expected = 2 * math.pi * (length - lower) / distance
    assert answer.solid_angle_sr == pytest.approx(expected, rel=1e-8)
    assert integrate([[0, 0, distance]], instrument_length_mm=lower - .001).solid_angle_sr == 0


def test_disjoint_components_and_target_at_entry():
    targets = [10 * direction(20, 0), 10 * direction(20, 180)]
    assert integrate(targets).solid_angle_sr == pytest.approx(
        4 * math.pi * (1 - math.sqrt(.96)), rel=1e-4)
    assert integrate([[0, 0, 0]]).solid_angle_sr == pytest.approx(cap_area(45), rel=1e-10)
    assert integrate([[0, 0, -10]]).solid_angle_sr == 0


def test_portal_cap_and_target_portal_intersection():
    radius, separation = 18., 23.
    # Target at entry isolates the footprint cap (the prior portal fixture).
    kwargs = dict(portal_normal=direction(12, 0), collision_radius_mm=1,
                  portal_radius_mm=1 / math.cos(math.radians(radius)))
    assert integrate([[0, 0, 0]], **kwargs).solid_angle_sr == pytest.approx(
        cap_area(radius), rel=1e-7)
    kwargs["portal_normal"] = direction(0, 0)
    result = integrate([10 * direction(separation, 0)],
                       reach_radius_mm=10 * math.sin(math.radians(radius)), **kwargs)
    assert result.solid_angle_sr == pytest.approx(equal_cap_lens(radius, separation), rel=1e-4)


def test_rotation_covariance():
    rng = np.random.default_rng(20261001)
    targets = np.array([10 * direction(11, 23), 10 * direction(19, 71)])
    baseline = integrate(targets)
    for _ in range(8):
        rotation, _ = np.linalg.qr(rng.normal(size=(3, 3)))
        answer = integrate(targets @ rotation.T, axis=rotation[:, 2],
                           portal_normal=rotation[:, 2])
        assert answer.solid_angle_sr == pytest.approx(baseline.solid_angle_sr, rel=1e-5)


def test_budgets_suppress_instead_of_returning_partial_area():
    limited = integrate([[0, 0, 10]], max_evaluations=1)
    assert limited.termination_reason == "evaluation_budget"
    assert limited.solid_angle_sr is None
    too_many = integrate([[0, 0, 10]] * 33)
    assert too_many.termination_reason == "target_limit"
    assert too_many.solid_angle_sr is None


def test_cancellation_propagates():
    def cancel():
        raise RuntimeError("cancelled")
    with pytest.raises(RuntimeError, match="cancelled"):
        integrate([[0, 0, 10]], cancel_check=cancel)


@pytest.mark.parametrize("kwargs", [
    {"max_evaluations": 0}, {"max_evaluations": 1.5}, {"max_evaluations": True},
    {"axis": [0, 0, 0]}, {"portal_normal": [0, 0, 0]}, {"half_angle_deg": 90},
    {"reach_radius_mm": -1}, {"max_seconds": math.inf},
    {"geometric_tolerance_mm": -1}, {"portal_radius_mm": 0},
])
def test_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        integrate([[0, 0, 10]], **kwargs)


def production_case(tilt=19., radius_deg=.1):
    return CorridorCase.model_validate({
        "case_id": "angular-production",
        "target": {"points_mm": [(10 * direction(tilt, 71)).tolist()]},
        "approaches": [{
            "name": "A", "kind": "eea",
            "portal": {"center_mm": [0, 0, 0], "normal": [0, 0, 1], "radius_mm": 2},
            "nominal_direction": [0, 0, 1],
            "instrument": {"length_mm": 20, "radius_mm": 0},
            "sampling": {"max_angle_deg": 45, "polar_steps": 5, "azimuth_steps": 24,
                         "target_directed": False, "adaptive_levels": 0},
            "target_tolerance_mm": 10 * math.sin(math.radians(radius_deg)),
        }],
    })


def test_production_integration_detects_cap_without_sampled_witness():
    result = analyze_case(production_case()).approaches[0]
    assert result.feasible_trajectory_count == 0
    assert result.feasible_solid_angle_sr == pytest.approx(cap_area(.1), rel=1e-4)
    assert any("geometry-aware" in note for note in result.notes)
    # Positive integrated area is not a stored insertion/pair witness.
    assert result.witness_direction is None


def test_production_protected_geometry_keeps_disclosed_grid_fallback():
    data = production_case().model_dump()
    data["protected_structures"] = [{
        "name": "far", "geometry": {"kind": "sphere", "center_mm": [100, 0, 0], "radius_mm": 1},
    }]
    result = analyze_case(CorridorCase.model_validate(data)).approaches[0]
    assert result.feasible_solid_angle_sr == 0
    assert any("1% accuracy is NOT established" in note for note in result.notes)


def test_production_target_limit_suppresses_measurement():
    data = production_case().model_dump()
    data["target"]["points_mm"] *= 33
    result = analyze_case(CorridorCase.model_validate(data)).approaches[0]
    assert result.feasible_solid_angle_sr is None
    assert any("target_limit" in note for note in result.notes)
