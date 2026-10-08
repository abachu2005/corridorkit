"""Reproducible numerical and cohort-level analysis."""

from skullbase_corridor.analysis.convergence import convergence_study
from skullbase_corridor.analysis.robustness import perturbation_study
from skullbase_corridor.analysis.statistics import paired_summary, subject_split

__all__ = [
    "convergence_study",
    "paired_summary",
    "perturbation_study",
    "subject_split",
]
