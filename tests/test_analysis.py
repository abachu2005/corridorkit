import numpy as np
import pytest

from corridorkit.analysis import convergence_study, paired_summary
from corridorkit.analysis.robustness import perturbation_study
from corridorkit.analysis.statistics import repeated_paired_summary, subject_split
from corridorkit.synthetic.cases import analytical_case


def test_convergence_is_deterministic_and_records_deltas():
    case = analytical_case()
    resolutions = [(3, 12), (5, 24)]
    first = convergence_study(case, resolutions)
    second = convergence_study(case, resolutions)
    assert first == second
    assert len(first["rows"]) == 4
    assert first["rows"][2]["coverage_delta"] is not None


def test_perturbation_study_is_seeded_and_not_called_confidence():
    case = analytical_case()
    first = perturbation_study(case, repeats=8, portal_sigma_mm=0.5, seed=4)
    second = perturbation_study(case, repeats=8, portal_sigma_mm=0.5, seed=4)
    assert first == second
    assert "not confidence intervals" in first["interpretation"]
    assert all(0 <= row["minimum"] <= row["maximum"] <= 1 for row in first["summaries"])


def test_subject_split_keeps_each_subject_atomic_and_stable():
    ids = ["patient-1", "patient-1", "patient-2", "patient-3"]
    assert subject_split(ids) == subject_split(reversed(ids))
    assert set(subject_split(ids)) == {"patient-1", "patient-2", "patient-3"}


def test_paired_summary_uses_subject_differences():
    result = paired_summary(
        {"a": 0.2, "b": 0.4, "c": 0.5},
        {"a": 0.4, "b": 0.5, "c": 0.9},
        bootstrap_repeats=1000,
        seed=2,
    )
    assert result["n_subjects"] == 3
    assert result["mean_paired_difference"] == pytest.approx(np.mean([0.2, 0.1, 0.4]))
    assert result["bootstrap_95_percent_interval"][0] <= result["mean_paired_difference"]
    assert result["bootstrap_95_percent_interval"][1] >= result["mean_paired_difference"]


@pytest.mark.parametrize("repeats", [0, 1, -2, 2.5, True])
def test_invalid_bootstrap_repeats(repeats):
    with pytest.raises(ValueError):
        paired_summary({"a": 1, "b": 2}, {"a": 2, "b": 3}, bootstrap_repeats=repeats)


def test_missing_unmatched_and_nonfinite_are_not_silent():
    with pytest.raises(ValueError, match="unmatched"):
        paired_summary({"a": 1, "b": 2, "c": 3}, {"a": 2, "b": 3})
    with pytest.raises(ValueError, match="nonfinite"):
        paired_summary({"a": 1, "b": np.nan}, {"a": 2, "b": 3})
    result = paired_summary({"a": 1, "b": 2, "c": None, "d": 4},
                            {"a": 2, "b": 3, "c": 3}, missing="exclude")
    assert result["excluded_invalid_subjects"] == ["c"]
    assert result["excluded_unmatched_subjects"] == ["d"]
    assert result["n_subjects"] == 2


def test_repeated_comparison_weights_subjects_not_targets():
    first = {"a": {"one": 0}, "b": {str(i): 0 for i in range(10)}}
    second = {"a": {"one": 1}, "b": {str(i): 0 for i in range(10)}}
    result = repeated_paired_summary(first, second, bootstrap_repeats=100)
    assert result["mean_paired_difference"] == 0.5
    assert result["n_subjects"] == 2
    with pytest.raises(ValueError, match="unmatched repeats"):
        repeated_paired_summary(first, {"a": {"different": 1}, "b": second["b"]})


@pytest.mark.parametrize("scale", [float("nan"), float("inf"), -1])
def test_perturbation_rejects_invalid_scales(scale):
    with pytest.raises(ValueError):
        perturbation_study(analytical_case(), repeats=2, registration_sigma_mm=scale)


def test_perturbation_unknown_anatomy_conservatively_abstains():
    data = analytical_case().model_dump(mode="json")
    data["protected_structures"] = [{"name": "missing", "status": "unknown"}]
    for approach in data["approaches"]:
        approach["allow_unknown_anatomy"] = True
    case = type(analytical_case()).model_validate(data)
    report = perturbation_study(case, repeats=2, registration_sigma_mm=1,
                                segmentation_inflation_mm=1)
    assert all(row["abstention_count"] == 2 for row in report["summaries"])
    assert all(row["mean_coverage_fraction"] == 0 for row in report["summaries"])


def test_convergence_rejects_bad_grid_and_reports_abstention_as_missing():
    with pytest.raises(ValueError):
        convergence_study(analytical_case(), [])
    with pytest.raises(ValueError):
        convergence_study(analytical_case(), [(5, 24), (3, 12)])
    data = analytical_case().model_dump(mode="json")
    data["protected_structures"] = [{"name": "missing", "status": "unknown"}]
    case = type(analytical_case()).model_validate(data)
    report = convergence_study(case, [(3, 12), (5, 24)])
    assert all(row["coverage_fraction"] is None for row in report["rows"])
    assert all(row["coverage_delta"] is None for row in report["rows"])
