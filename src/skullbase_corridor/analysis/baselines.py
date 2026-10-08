"""Auditable numerical references and actual-engine radius ablations.

The zero-radius base ray/cone is a finite-insertion geometric ablation, NOT an
endoscope visibility model. No tissue, image formation or physical anatomy is
inferred. Analytic expectation helpers do not import production geometry.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.optimize import minimize_scalar

from skullbase_corridor.domain.models import CorridorCase


def parameter_hash(parameters) -> str:
    encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def optimized_capsule_sphere_clearance(start, end, capsule_radius, center, sphere_radius):
    """Independent bounded scalar minimization plus endpoint candidates.

    No imports from geometry.primitives or geometry.reference. Returns signed
    sphere/capsule surface clearance, not signed distance to arbitrary meshes.
    """
    points = np.asarray([start, end, center], dtype=float)
    radii = np.asarray([capsule_radius, sphere_radius], dtype=float)
    if (points.shape != (3, 3) or not np.all(np.isfinite(points))
            or not np.all(np.isfinite(radii)) or np.any(radii < 0)):
        raise ValueError("points and nonnegative radii must be finite")
    def squared(parameter):
        residual = (1 - parameter) * points[0] + parameter * points[1] - points[2]
        return float(residual @ residual)
    optimum = minimize_scalar(squared, bounds=(0., 1.), method="bounded",
                              options={"xatol": 1e-14})
    if not optimum.success:
        raise RuntimeError(f"independent scalar minimization failed: {optimum.message}")
    return math.sqrt(max(0., min(squared(0.), squared(1.), optimum.fun))) - float(radii.sum())


def analytic_cap_solid_angle(half_angle_deg):
    angle = float(half_angle_deg)
    if not math.isfinite(angle) or not 0 <= angle <= 180:
        raise ValueError("half angle must be finite and in [0, 180]")
    return 2 * math.pi * (1 - math.cos(math.radians(angle)))


def off_axis_target_solid_angle(distance_mm, tolerance_mm, offset_deg, sampling_cap_deg):
    """Exact area of a target-tolerance angular cap wholly inside the sampled cap.

    Requires a forward target and sufficient instrument length (>= target
    distance), no obstacles/aperture clipping, and zero working-tip radius.
    No formula for the partially clipped lens is implied.
    """
    values = np.asarray([distance_mm, tolerance_mm, offset_deg, sampling_cap_deg], dtype=float)
    if not np.all(np.isfinite(values)):
        raise ValueError("parameters must be finite")
    if not 0 <= tolerance_mm < distance_mm or not 0 <= offset_deg < sampling_cap_deg < 90:
        raise ValueError("require 0 <= tolerance < distance and 0 <= offset < cap < 90")
    target_half_angle = math.degrees(math.asin(tolerance_mm / distance_mm))
    if offset_deg + target_half_angle > sampling_cap_deg:
        raise ValueError("target angular cap must be wholly inside the sampling cap")
    return analytic_cap_solid_angle(target_half_angle)


def run_engine_ablations(case: CorridorCase, *, base_directory: Path | None = None,
                         minimum_angle_deg=0.) -> list[dict]:
    """Run all three models with identical target-directed direction sampling.

    Offsets/adaptation are disabled for an exactly shared direction/entry candidate
    set: radius-dependent usable offset disks otherwise confound the ablation.
    Finite-shaft and simultaneous use exactly the same case/instruments. The
    simultaneous model additionally evaluates shaft-shaft pairs.
    """
    from skullbase_corridor.geometry.engine import analyze_case

    original = CorridorCase.model_validate(case.model_dump()).model_dump(mode="json")
    rows = []
    for model in ("base_ray_cone", "finite_shaft", "simultaneous"):
        data = json.loads(json.dumps(original))
        for approach in data["approaches"]:
            approach["sampling"].update(
                target_directed=True, adaptive_levels=0, portal_offset_rings=0,
            )
            if approach["instrument"]["tip_working_radius_mm"] != 0:
                raise ValueError("controlled ablations require zero working-tip radius")
            if model == "base_ray_cone":
                approach["instrument"]["radius_mm"] = 0.
        parameters = {"case": data, "minimum_angle_deg": minimum_angle_deg
                      if model == "simultaneous" else None}
        started = perf_counter()
        row = {"model": model, "parameter_hash": parameter_hash(parameters),
               "parameters": parameters, "error": None}
        try:
            result = analyze_case(
                CorridorCase.model_validate(data), base_directory=base_directory,
                simultaneous_minimum_angle_deg=minimum_angle_deg if model == "simultaneous" else None,
            )
            row["result"] = result.model_dump(mode="json")
            row["statuses"] = {
                "approaches": [a.status.value for a in result.approaches],
                "pairs": [p.status.value for p in result.simultaneous_pairs],
            }
        except Exception as exc:
            # Keep failures as evidence; never silently drop an unfavorable model.
            row.update(result=None, statuses={"execution": "error"},
                       error={"type": type(exc).__name__, "message": str(exc)})
        row["runtime_seconds"] = perf_counter() - started
        rows.append(row)
    return rows
