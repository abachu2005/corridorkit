"""Subject-level splitting and paired descriptive inference."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping

import numpy as np


def subject_split(
    subject_ids: Iterable[str],
    *,
    development_fraction: float = 0.6,
    validation_fraction: float = 0.2,
    salt: str = "skullbase-corridor-v1",
) -> dict[str, str]:
    """Stable subject-level assignment that cannot split sides or targets."""
    if not (0 < development_fraction < 1):
        raise ValueError("development_fraction must be between zero and one")
    if not (0 <= validation_fraction < 1 - development_fraction):
        raise ValueError("validation_fraction leaves no evaluation allocation")
    result = {}
    for subject_id in sorted(set(subject_ids)):
        digest = hashlib.sha256(f"{salt}:{subject_id}".encode()).digest()
        value = int.from_bytes(digest[:8], "big") / 2**64
        if value < development_fraction:
            partition = "development"
        elif value < development_fraction + validation_fraction:
            partition = "validation"
        else:
            partition = "evaluation"
        result[subject_id] = partition
    return result


def paired_summary(
    first: Mapping[str, float],
    second: Mapping[str, float],
    *,
    bootstrap_repeats: int = 5000,
    seed: int = 20261001,
    missing: str = "error",
) -> dict:
    """Paired subject-level effect summary with a percentile bootstrap interval."""
    if isinstance(bootstrap_repeats, bool) or not isinstance(bootstrap_repeats, int) or bootstrap_repeats < 2:
        raise ValueError("bootstrap_repeats must be an integer at least 2")
    if missing not in {"error", "exclude"}:
        raise ValueError("missing must be 'error' or explicit 'exclude'")
    unmatched = sorted(set(first) ^ set(second))
    invalid = []
    for key in sorted(set(first) & set(second)):
        try:
            valid = bool(np.isfinite(float(first[key])) and np.isfinite(float(second[key])))
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            invalid.append(key)
    if missing == "error" and (unmatched or invalid):
        raise ValueError(f"unmatched subjects: {unmatched}; missing/nonfinite values: {invalid}")
    subjects = sorted(set(first) & set(second))
    subjects = [subject for subject in subjects if subject not in invalid]
    if len(subjects) < 2:
        raise ValueError("at least two paired subjects are required")
    differences = np.asarray([float(second[key]) - float(first[key]) for key in subjects])
    if not np.all(np.isfinite(differences)):
        raise ValueError("paired differences overflow finite range")
    rng = np.random.default_rng(seed)
    sampled = rng.choice(differences, size=(bootstrap_repeats, len(differences)), replace=True)
    means = sampled.mean(axis=1)
    return {
        "n_subjects": len(subjects),
        "subject_ids": subjects,
        "paired_differences": dict(zip(subjects, differences.tolist(), strict=True)),
        "excluded_unmatched_subjects": unmatched,
        "excluded_invalid_subjects": invalid,
        "mean_paired_difference": float(differences.mean()),
        "median_paired_difference": float(np.median(differences)),
        "sample_standard_deviation": float(np.std(differences, ddof=1)),
        "minimum": float(differences.min()),
        "maximum": float(differences.max()),
        "bootstrap_95_percent_interval": [
            float(np.quantile(means, 0.025)),
            float(np.quantile(means, 0.975)),
        ],
        "bootstrap_repeats": bootstrap_repeats,
        "seed": seed,
        "interpretation": (
            "Paired descriptive effect across subjects; target voxels are not "
            "treated as independent observations."
        ),
    }


def repeated_paired_summary(
    first: Mapping[str, Mapping[str, float]],
    second: Mapping[str, Mapping[str, float]],
    *,
    bootstrap_repeats: int = 5000,
    seed: int = 20261001,
) -> dict:
    """Average matched repeat differences within subjects, then weight subjects equally.

    Explicit repeat keys prevent accidental pairing of different sides, targets,
    or scenarios. No repeats or subjects are silently dropped.
    """
    if set(first) != set(second):
        raise ValueError("unmatched subjects")
    subject_effects, counts = {}, {}
    for subject in first:
        a, b = first[subject], second[subject]
        if not a or set(a) != set(b):
            raise ValueError(f"missing or unmatched repeats for subject {subject}")
        try:
            pairs = np.asarray([(float(a[key]), float(b[key])) for key in sorted(a)])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"invalid repeated values for subject {subject}") from exc
        if not np.all(np.isfinite(pairs)):
            raise ValueError(f"nonfinite repeated values for subject {subject}")
        subject_effects[subject] = float(np.mean(pairs[:, 1] - pairs[:, 0]))
        counts[subject] = len(a)
    result = paired_summary(
        dict.fromkeys(subject_effects, 0.0), subject_effects,
        bootstrap_repeats=bootstrap_repeats, seed=seed,
    )
    result.update(
        within_subject_repeat_counts=counts,
        weighting="equal subjects after mean of exactly matched within-subject differences",
    )
    return result
