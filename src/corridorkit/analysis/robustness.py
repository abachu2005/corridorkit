"""Deterministic perturbation sensitivity, distinct from clinical probability."""

from __future__ import annotations

import numpy as np
from pathlib import Path

from corridorkit.domain.models import CorridorCase
from corridorkit.geometry.engine import analyze_case


def _perturb_case(
    case: CorridorCase, rng: np.random.Generator, portal_sigma_mm: float
) -> CorridorCase:
    data = case.model_dump(mode="json")
    for approach in data["approaches"]:
        center = np.asarray(approach["portal"]["center_mm"], dtype=float)
        approach["portal"]["center_mm"] = (
            center + rng.normal(0.0, portal_sigma_mm, size=3)
        ).tolist()
    return CorridorCase.model_validate(data)


def perturbation_study(
    case: CorridorCase,
    *,
    repeats: int = 200,
    portal_sigma_mm: float = 1.0,
    registration_sigma_mm: float = 0.0,
    segmentation_inflation_mm: float = 0.0,
    seed: int = 20261001,
    base_directory: Path | None = None,
) -> dict:
    """Summarize sensitivity under a documented assumed portal perturbation."""
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 2:
        raise ValueError("repeats must be an integer at least 2")
    for value in (portal_sigma_mm, registration_sigma_mm, segmentation_inflation_mm):
        if not np.isfinite(value) or value < 0:
            raise ValueError("perturbation scales must be finite and nonnegative")
    rng = np.random.default_rng(seed)
    samples: dict[str, list[float]] = {approach.name: [] for approach in case.approaches}
    if len(samples) != len(case.approaches):
        raise ValueError("approach names must be unique")
    abstentions = dict.fromkeys(samples, 0)
    records = []
    for _ in range(repeats):
        perturbed = _perturb_case(case, rng, portal_sigma_mm)
        data = perturbed.model_dump(mode="json")
        for approach in data["approaches"]:
            approach["allow_unknown_anatomy"] = False
        translation = rng.normal(0.0, registration_sigma_mm, size=3)
        data["target"]["points_mm"] = (
            np.asarray(data["target"]["points_mm"]) + translation
        ).tolist()
        unsupported = False
        for structure in data["protected_structures"]:
            geometry = structure.get("geometry")
            if geometry and segmentation_inflation_mm:
                if geometry["kind"] == "sphere":
                    geometry["radius_mm"] += segmentation_inflation_mm
                else:
                    unsupported = True
        if unsupported:
            for name in samples:
                samples[name].append(0.0)
                abstentions[name] += 1
            records.append({"status": "abstained", "reason": "segmentation_perturbation_unsupported_geometry"})
            continue
        result = analyze_case(CorridorCase.model_validate(data), base_directory=base_directory)
        repeat_record = {"registration_translation_mm": translation.tolist(), "approaches": []}
        for approach in result.approaches:
            abstained = str(approach.status) in ("abstained", "incomplete", "unsupported") or approach.target_count <= 0
            value = 0.0 if abstained else len(approach.reached_point_indices) / approach.target_count
            samples[approach.name].append(value)
            abstentions[approach.name] += int(abstained)
            repeat_record["approaches"].append({
                "name": approach.name, "status": str(approach.status),
                "demonstrated_coverage_fraction": value,
            })
        records.append(repeat_record)
    summaries = []
    for name, values in samples.items():
        array = np.asarray(values)
        summaries.append(
            {
                "approach": name,
                "abstention_count": abstentions[name],
                "abstention_fraction": abstentions[name] / repeats,
                "mean_coverage_fraction": float(np.mean(array)),
                "standard_deviation": float(np.std(array, ddof=1)),
                "sensitivity_interval_2_5_97_5": [
                    float(np.quantile(array, 0.025)),
                    float(np.quantile(array, 0.975)),
                ],
                "minimum": float(np.min(array)),
                "maximum": float(np.max(array)),
            }
        )
    return {
        "schema_version": "1.0",
        "case_id": case.case_id,
        "assumption": {
            "portal_center_error_distribution": "independent Gaussian per axis",
            "portal_sigma_mm": portal_sigma_mm,
            "registration_sigma_mm": registration_sigma_mm,
            "registration_model": "one shared relative target translation per repeat; not measured registration error",
            "segmentation_inflation_mm": segmentation_inflation_mm,
            "segmentation_model": "outward sphere boundary offset; unsupported geometry abstains",
            "repeats": repeats,
            "seed": seed,
        },
        "summaries": summaries,
        "repeat_records": records,
        "abstention_policy": "zero demonstrated coverage with separate status; not a claim of zero true coverage",
        "interpretation": (
            "Intervals describe sensitivity to the stated perturbation model. "
            "They are not confidence intervals or probabilities of surgical safety."
        ),
    }
