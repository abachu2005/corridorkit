"""Independent discretized reference checks for analytic geometry."""

from __future__ import annotations

import numpy as np


def sampled_capsule_sphere_clearance(
    start: np.ndarray,
    end: np.ndarray,
    capsule_radius: float,
    sphere_center: np.ndarray,
    sphere_radius: float,
    *,
    samples: int = 100_001,
) -> float:
    """Brute-force centerline sampling, intended for validation rather than use."""
    if samples < 2:
        raise ValueError("samples must be at least two")
    parameters = np.linspace(0.0, 1.0, samples)
    points = start[None, :] + parameters[:, None] * (end - start)[None, :]
    return float(np.min(np.linalg.norm(points - sphere_center[None, :], axis=1))) - (
        capsule_radius + sphere_radius
    )


def sampled_segment_distance(
    first_start: np.ndarray,
    first_end: np.ndarray,
    second_start: np.ndarray,
    second_end: np.ndarray,
    *,
    samples: int = 1001,
) -> float:
    """Grid reference for tests; intentionally independent and slow."""
    parameters = np.linspace(0.0, 1.0, samples)
    first = first_start[None, :] + parameters[:, None] * (first_end - first_start)[None, :]
    second = second_start[None, :] + parameters[:, None] * (second_end - second_start)[None, :]
    best = float("inf")
    block = 100
    for offset in range(0, samples, block):
        distances = np.linalg.norm(
            first[offset : offset + block, None, :] - second[None, :, :], axis=2
        )
        best = min(best, float(np.min(distances)))
    return best
