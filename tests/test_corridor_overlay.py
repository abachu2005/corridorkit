import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from corridorkit.desktop.corridor_overlay import capsule_union_slice
from corridorkit.desktop.trajectory_geometry import SelectedTrajectory


def test_union_preserves_gap_between_feasible_paths():
    paths = [
        SelectedTrajectory("a", i, np.array([x, 0., 0.]), np.array([x, 10., 0.]), ())
        for i, x in enumerate((-5., 5.))
    ]
    mask, (x0, y0, width, height) = capsule_union_slice(
        paths, 1., (0, 1), 0., spacing=.25)
    xs = x0 + (np.arange(mask.shape[1]) + .5) * width / mask.shape[1]
    assert not mask[:, np.abs(xs) < 3].any()
    assert mask.any()
    assert capsule_union_slice(paths, 1., (0, 1), 4.) is None
    assert capsule_union_slice([], 1., (0, 1), 0.) is None
