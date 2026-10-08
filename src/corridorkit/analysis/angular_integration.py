"""Standalone experimental adaptive solid-angle quadrature, not engine analysis.

Equal-area coordinates are u=1-cos(theta), phi=azimuth, so dOmega=du*dphi.
Every cell uses a midpoint and its four equal-area child midpoints. Mixed sampled
cells receive priority; periodic largest-cell exploration reduces (but cannot
eliminate) missed islands. No regularity assumption is made about the callback:
error indicators and sampled boundary area are HEURISTIC, never certified bounds.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from time import perf_counter
from typing import Callable

import numpy as np


@dataclass(frozen=True)
class AngularIntegrationResult:
    solid_angle_sr: float
    domain_solid_angle_sr: float
    estimated_error_sr: float
    sampled_boundary_area_sr: float
    last_successive_change_sr: float | None
    sample_count: int
    sample_budget: int
    leaf_count: int
    boundary_refinements: int
    exploration_refinements: int
    elapsed_seconds: float
    termination_reason: str
    heuristic_tolerance_met: bool
    error_is_certified: bool = False
    rigorous_error_bound_sr: float | None = None
    interpretation: str = (
        "Standalone experimental quadrature. Estimated error, successive change, and "
        "sampled boundary area are heuristic: uniform samples can miss entire islands "
        "or boundaries. No certified accuracy, confidence, or completeness."
    )


@dataclass
class _Cell:
    u0: float
    u1: float
    p0: float
    p1: float
    depth: int
    center: bool
    children: tuple[bool, bool, bool, bool]

    @property
    def area(self):
        return (self.u1 - self.u0) * (self.p1 - self.p0)

    @property
    def estimate(self):
        return self.area * sum(self.children) / 4

    @property
    def error(self):
        return abs(self.estimate - self.area * self.center)

    @property
    def mixed(self):
        return min((self.center, *self.children)) != max((self.center, *self.children))

    def bounds(self):
        um, pm = (self.u0 + self.u1) / 2, (self.p0 + self.p1) / 2
        return [(a, b, c, d) for a, b in ((self.u0, um), (um, self.u1))
                for c, d in ((self.p0, pm), (pm, self.p1))]


def integrate_solid_angle(
    feasible: Callable[[np.ndarray], bool],
    *,
    axis=(0., 0., 1.),
    half_angle_deg=45.,
    absolute_tolerance_sr=2e-4,
    max_samples=100_000,
    max_seconds=10.,
    initial_u_cells=8,
    initial_azimuth_cells=32,
    minimum_uniform_depth=1,
    max_depth=12,
    exploration_interval=8,
) -> AngularIntegrationResult:
    """Integrate a scalar Boolean direction predicate on a spherical cap.

    Directions are physical unit vectors, ordered deterministically. Predicates
    must return bool/np.bool_; unknown/incomplete outcomes must not be coerced to
    false. Exceptions propagate. Budgets count actual callback evaluations.
    The deadline is cooperative between calls: a slow callback cannot be
    interrupted. Partial initial-grid results are not returned.

    Stop when both the sum of nested absolute cell differences <= tolerance and
    sampled-mixed-cell area <= 4*tolerance, OR a budget/depth limit is reached.
    Neither condition is an error bound without further predicate assumptions.
    """
    started = perf_counter()
    axis = np.asarray(axis, dtype=float)
    if axis.shape != (3,) or not np.all(np.isfinite(axis)):
        raise ValueError("axis must contain three finite values")
    norm = float(np.linalg.norm(axis))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("axis must be nonzero with finite norm")
    axis = axis / norm
    for name, value in (("half_angle_deg", half_angle_deg),
                        ("absolute_tolerance_sr", absolute_tolerance_sr),
                        ("max_seconds", max_seconds)):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if half_angle_deg > 180:
        raise ValueError("half_angle_deg must be at most 180")
    integers = {
        "max_samples": max_samples, "initial_u_cells": initial_u_cells,
        "initial_azimuth_cells": initial_azimuth_cells, "max_depth": max_depth,
        "exploration_interval": exploration_interval,
    }
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in integers.values()):
        raise ValueError("budgets, grid sizes, depth and exploration interval must be positive integers")
    if (isinstance(minimum_uniform_depth, bool) or not isinstance(minimum_uniform_depth, int)
            or not 0 <= minimum_uniform_depth <= max_depth):
        raise ValueError("minimum_uniform_depth must be an integer within [0, max_depth]")
    initial_cost = 5 * initial_u_cells * initial_azimuth_cells
    if max_samples < initial_cost:
        raise ValueError("sample budget cannot cover the initial grid")
    helper = np.eye(3)[int(np.argmin(np.abs(axis)))]
    first = np.cross(axis, helper)
    first /= np.linalg.norm(first)
    second = np.cross(axis, first)
    cap_u = 1 - math.cos(math.radians(half_angle_deg))
    samples = 0

    def evaluate(u, phi):
        nonlocal samples
        cosine = 1 - u
        sine = math.sqrt(max(0., 1 - cosine * cosine))
        direction = cosine * axis + sine * (math.cos(phi) * first + math.sin(phi) * second)
        answer = feasible(direction)
        samples += 1
        if not isinstance(answer, (bool, np.bool_)):
            raise ValueError("feasibility callback must return bool, not numeric/unknown status")
        return bool(answer)

    def make_cell(bounds, depth, center=None):
        u0, u1, p0, p1 = bounds
        um, pm = (u0 + u1) / 2, (p0 + p1) / 2
        if center is None:
            center = evaluate(um, pm)
        child_values = tuple(evaluate(u, p)
                             for u in ((u0 + um) / 2, (um + u1) / 2)
                             for p in ((p0 + pm) / 2, (pm + p1) / 2))
        return _Cell(u0, u1, p0, p1, depth, center, child_values)

    leaves = {}
    uniform_heap, boundary_heap, mandatory_heap = [], [], []
    serial = 0
    estimate = error = boundary_area = 0.

    def add(cell):
        nonlocal serial, estimate, error, boundary_area
        serial += 1
        leaves[serial] = cell
        estimate += cell.estimate
        error += cell.error
        boundary_area += cell.area if cell.mixed else 0.
        if cell.depth < max_depth:
            heapq.heappush(uniform_heap, (-cell.area, serial))
            if cell.mixed:
                heapq.heappush(boundary_heap, (-cell.area, serial))
            if cell.depth < minimum_uniform_depth:
                heapq.heappush(mandatory_heap, (cell.depth, serial))

    for i in range(initial_u_cells):
        for j in range(initial_azimuth_cells):
            if perf_counter() - started >= max_seconds:
                raise TimeoutError("deadline reached before completing initial grid")
            add(make_cell((cap_u * i / initial_u_cells, cap_u * (i + 1) / initial_u_cells,
                           2 * math.pi * j / initial_azimuth_cells,
                           2 * math.pi * (j + 1) / initial_azimuth_cells), 0))

    def peek(heap):
        while heap and heap[0][1] not in leaves:
            heapq.heappop(heap)
        return heap[0][1] if heap else None

    boundary_refinements = exploration_refinements = 0
    last_change = None
    reason = "sample_budget"
    tolerance_met = False
    while True:
        mandatory_id = peek(mandatory_heap)
        tolerance_met = (mandatory_id is None and error <= absolute_tolerance_sr
                         and boundary_area <= 4 * absolute_tolerance_sr)
        if tolerance_met:
            reason = "heuristic_tolerance"
            break
        if samples + 16 > max_samples:
            reason = "sample_budget"
            break
        if perf_counter() - started >= max_seconds:
            reason = "time_budget"
            break
        count = boundary_refinements + exploration_refinements
        cell_id = mandatory_id
        is_boundary = False
        if cell_id is None:
            if count % exploration_interval != 0:
                cell_id = peek(boundary_heap)
                is_boundary = cell_id is not None
            if cell_id is None:
                cell_id = peek(uniform_heap)
        if cell_id is None:
            reason = "depth_limit"
            break
        cell = leaves[cell_id]
        # Complete the four-cell replacement atomically; max 16 callbacks beyond
        # the last deadline check. Child centers reuse their parent's evaluations.
        children = [make_cell(bounds, cell.depth + 1, center)
                    for bounds, center in zip(cell.bounds(), cell.children)]
        del leaves[cell_id]
        estimate -= cell.estimate
        error -= cell.error
        boundary_area -= cell.area if cell.mixed else 0.
        previous = estimate + cell.estimate
        for child in children:
            add(child)
        last_change = abs(estimate - previous)
        if is_boundary:
            boundary_refinements += 1
        else:
            exploration_refinements += 1
    return AngularIntegrationResult(
        solid_angle_sr=float(np.clip(estimate, 0, 2 * math.pi * cap_u)),
        domain_solid_angle_sr=2 * math.pi * cap_u,
        estimated_error_sr=max(0., error),
        sampled_boundary_area_sr=max(0., boundary_area),
        last_successive_change_sr=last_change, sample_count=samples,
        sample_budget=max_samples, leaf_count=len(leaves),
        boundary_refinements=boundary_refinements,
        exploration_refinements=exploration_refinements,
        elapsed_seconds=perf_counter() - started, termination_reason=reason,
        heuristic_tolerance_met=tolerance_met,
    )
