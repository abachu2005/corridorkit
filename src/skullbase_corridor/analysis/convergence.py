"""Resolution-convergence analysis for sampled corridor geometry."""

from __future__ import annotations

from typing import Iterable
from pathlib import Path

from skullbase_corridor.domain.models import CorridorCase
from skullbase_corridor.geometry.engine import analyze_case


def convergence_study(
    case: CorridorCase,
    resolutions: Iterable[tuple[int, int]],
    *, base_directory: Path | None = None,
) -> dict:
    """Evaluate approach summaries at fixed, increasingly dense resolutions."""
    resolutions = list(resolutions)
    if not resolutions:
        raise ValueError("at least one resolution is required")
    for polar, azimuth in resolutions:
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1
               for value in (polar, azimuth)):
            raise ValueError("resolutions must contain positive integers")
    if any(a[0] > b[0] or a[1] > b[1] or a == b
           for a, b in zip(resolutions, resolutions[1:])):
        raise ValueError("resolutions must increase without duplicates")
    rows = []
    previous: dict[str, dict] = {}
    for polar_steps, azimuth_steps in resolutions:
        data = case.model_dump(mode="json")
        for approach in data["approaches"]:
            approach["allow_unknown_anatomy"] = False
            approach["sampling"]["polar_steps"] = polar_steps
            approach["sampling"]["azimuth_steps"] = azimuth_steps
        configured = CorridorCase.model_validate(data)
        result = analyze_case(configured, base_directory=base_directory)
        for approach in result.approaches:
            abstained = str(approach.status) in ("abstained", "incomplete", "unsupported") or approach.target_count <= 0
            coverage = None if abstained else len(approach.reached_point_indices) / approach.target_count
            prior = previous.get(approach.name)
            row = {
                "approach": approach.name,
                "status": str(approach.status),
                "polar_steps": polar_steps,
                "azimuth_steps": azimuth_steps,
                "sample_count": len(approach.trajectories),
                "coverage_fraction": coverage,
                "solid_angle_sr": None if abstained else approach.feasible_solid_angle_sr,
                "coverage_delta": (
                    None if prior is None or coverage is None or prior["coverage_fraction"] is None
                    else coverage - prior["coverage_fraction"]
                ),
                "solid_angle_delta_sr": (
                    None if prior is None or abstained or prior["solid_angle_sr"] is None
                    or approach.feasible_solid_angle_sr is None
                    else approach.feasible_solid_angle_sr - prior["solid_angle_sr"]
                ),
            }
            rows.append(row)
            previous[approach.name] = row
    return {
        "schema_version": "1.0",
        "case_id": case.case_id,
        "rows": rows,
        "interpretation": (
            "Small successive differences support numerical stability only at the "
            "tested resolutions; they do not certify that an unsampled path is absent."
        ),
    }
