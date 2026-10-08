import math

import numpy as np
import pytest

from skullbase_corridor.geometry.primitives import (
    angle_degrees,
    capsule_capsule_clearance,
    capsule_sphere_clearance,
    portal_allows_direction,
    sample_spherical_cap,
)
from skullbase_corridor.geometry.reference import sampled_capsule_sphere_clearance


def test_angles_and_deterministic_sampling():
    assert angle_degrees(np.array([1.0, 0, 0]), np.array([0.0, 1, 0])) == pytest.approx(90)
    first = sample_spherical_cap(np.array([0.0, 0, 1]), 30, 4, 12)
    second = sample_spherical_cap(np.array([0.0, 0, 1]), 30, 4, 12)
    assert np.array_equal(first, second)
    assert len(first) == 37
    assert max(angle_degrees(v, np.array([0.0, 0, 1])) for v in first) == pytest.approx(30)


def test_capsule_sphere_tangency_matches_reference():
    start, end = np.array([0.0, 0, 0]), np.array([10.0, 0, 0])
    center = np.array([5.0, 3.0, 0])
    analytical = capsule_sphere_clearance(start, end, 1.0, center, 2.0)
    brute = sampled_capsule_sphere_clearance(start, end, 1.0, center, 2.0)
    assert analytical == pytest.approx(0.0, abs=1e-12)
    assert brute == pytest.approx(analytical, abs=1e-8)


def test_portal_aperture_accounts_for_obliquity_and_radius():
    normal = np.array([0.0, 0, 1])
    assert portal_allows_direction(normal, normal, 2.0, 2.0)
    direction = np.array([math.sqrt(3) / 2, 0.0, 0.5])
    assert portal_allows_direction(direction, normal, 3.0, 1.0)
    assert not portal_allows_direction(direction, normal, 1.9, 1.0)


def test_simultaneous_crossing_capsules_collide():
    clearance = capsule_capsule_clearance(
        np.array([-1.0, 0, 0]), np.array([1.0, 0, 0]), 0.2,
        np.array([0.0, -1, 0]), np.array([0.0, 1, 0]), 0.2,
    )
    assert clearance == pytest.approx(-0.4)
