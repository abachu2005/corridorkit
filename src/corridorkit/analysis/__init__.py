"""Reproducible numerical and cohort-level analysis."""

from corridorkit.analysis.convergence import convergence_study
from corridorkit.analysis.robustness import perturbation_study
from corridorkit.analysis.statistics import paired_summary, subject_split

__all__ = [
    "convergence_study",
    "paired_summary",
    "perturbation_study",
    "subject_split",
]
