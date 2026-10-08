import math

import numpy as np
import pytest

from corridorkit.geometry.primitives import spherical_cap_sample_weights


@pytest.mark.parametrize("polar,azimuth", [(1, 1), (3, 8), (9, 48), (17, 96)])
def test_spherical_cap_weights_match_exact_area(polar, azimuth):
    angle = 35.0
    weights = spherical_cap_sample_weights(angle, polar, azimuth)
    assert len(weights) == 1 if polar == 1 else 1 + (polar - 1) * azimuth
    assert np.all(weights > 0)
    assert weights.sum() == pytest.approx(
        2 * math.pi * (1 - math.cos(math.radians(angle)))
    )
