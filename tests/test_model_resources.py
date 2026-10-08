from dataclasses import replace

import pytest

from skullbase_corridor.anatomy.resources import (
    ArtifactAvailability,
    LicenseStatus,
    Modality,
    REGISTRY_SCHEMA_VERSION,
    RESOURCE_REGISTRY,
    ResourceRole,
    SupportStatus,
    filter_resources,
    get_resource,
    validate_registry,
)


EXPECTED_RESOURCE_IDS = {
    "totalsegmentator-head-glands-cavities",
    "totalsegmentator-head-muscles",
    "totalsegmentator-headneck-bones-vessels",
    "totalsegmentator-headneck-muscles",
    "totalsegmentator-craniofacial",
    "totalsegmentator-teeth",
    "nasalseg",
    "slicer-automated-dental-tools-amasss",
    "slicer-automated-dental-tools-ali",
    "toothfairy3",
    "ct-scope",
    "han-seg",
    "segrap2023",
    "topcow",
    "uw-skullbase-atlas",
    "alpaca",
    "cntseg",
    "opticnerve-seg",
    "sinussegment",
}


def test_registry_is_versioned_and_contains_every_required_resource() -> None:
    assert REGISTRY_SCHEMA_VERSION == "1.0.0"
    assert {resource.resource_id for resource in RESOURCE_REGISTRY} == EXPECTED_RESOURCE_IDS


def test_registry_entries_have_typed_complete_evidence_metadata() -> None:
    for resource in RESOURCE_REGISTRY:
        assert resource.version
        assert resource.roles
        assert all(isinstance(role, ResourceRole) for role in resource.roles)
        assert resource.modalities
        assert all(isinstance(modality, Modality) for modality in resource.modalities)
        assert resource.relevant_labels
        assert isinstance(resource.artifact_availability, ArtifactAvailability)
        assert resource.artifact_notes
        assert resource.license_id
        assert isinstance(resource.license_status, LicenseStatus)
        assert resource.source_url.startswith("https://")
        assert resource.limitations
        assert isinstance(resource.support_status, SupportStatus)


def test_filter_by_modality_accepts_enum_and_string() -> None:
    expected = filter_resources(modality=Modality.CBCT)
    assert expected
    assert expected == filter_resources(modality="cbct")
    assert all(Modality.CBCT in resource.modalities for resource in expected)
    assert {resource.resource_id for resource in expected} == {
        "slicer-automated-dental-tools-amasss",
        "slicer-automated-dental-tools-ali",
        "totalsegmentator-teeth",
        "toothfairy3",
    }


def test_filter_by_role_accepts_enum_and_string() -> None:
    unavailable = filter_resources(role=ResourceRole.UNAVAILABLE)
    assert unavailable == filter_resources(role="unavailable")
    assert {resource.resource_id for resource in unavailable} == {
        "cntseg",
        "sinussegment",
    }
    assert all(ResourceRole.UNAVAILABLE in resource.roles for resource in unavailable)


def test_combined_filters_use_and_semantics() -> None:
    ct_evaluation = filter_resources(modality="ct", role="evaluation")
    assert ct_evaluation
    assert all(
        Modality.CT in resource.modalities and ResourceRole.EVALUATION in resource.roles
        for resource in ct_evaluation
    )
    assert {resource.resource_id for resource in ct_evaluation} == {
        "nasalseg",
        "ct-scope",
        "han-seg",
        "segrap2023",
        "uw-skullbase-atlas",
    }


def test_no_filters_returns_immutable_registry_tuple() -> None:
    assert filter_resources() == RESOURCE_REGISTRY
    assert isinstance(filter_resources(), tuple)


def test_invalid_filter_value_raises_value_error() -> None:
    with pytest.raises(ValueError):
        filter_resources(modality="pet")
    with pytest.raises(ValueError):
        filter_resources(role="production")


def test_get_resource_and_missing_resource() -> None:
    resource = get_resource("topcow")
    assert resource.name == "TopCoW"
    assert resource.modalities == frozenset({Modality.CTA, Modality.MRA})
    with pytest.raises(KeyError, match="not-a-resource"):
        get_resource("not-a-resource")


def test_downloadable_upstream_artifact_does_not_imply_local_support() -> None:
    sinussegment = get_resource("sinussegment")
    assert sinussegment.artifact_availability is ArtifactAvailability.DOWNLOADABLE
    assert sinussegment.support_status is SupportStatus.UNAVAILABLE
    assert sinussegment.production_default is False

    totalsegmentator = get_resource("totalsegmentator-head-glands-cavities")
    assert totalsegmentator.artifact_availability is ArtifactAvailability.DOWNLOADABLE
    assert totalsegmentator.support_status is SupportStatus.RESEARCH_ONLY
    assert totalsegmentator.production_default is False


def test_data_resources_are_not_misrepresented_as_models() -> None:
    for resource_id in (
        "nasalseg",
        "toothfairy3",
        "han-seg",
        "opticnerve-seg",
        "uw-skullbase-atlas",
    ):
        resource = get_resource(resource_id)
        assert resource.artifact_availability is ArtifactAvailability.DATA_ONLY
        assert resource.support_status is SupportStatus.RESEARCH_ONLY


def test_unknown_or_ambiguous_licenses_are_explicit() -> None:
    assert get_resource("cntseg").license_status is LicenseStatus.UNKNOWN
    assert get_resource("sinussegment").license_status is LicenseStatus.UNKNOWN
    assert (
        get_resource("slicer-automated-dental-tools-amasss").license_status
        is LicenseStatus.REVIEW_REQUIRED
    )
    assert get_resource("segrap2023").license_status is LicenseStatus.REVIEW_REQUIRED


def test_builtin_registry_passes_validation_and_has_no_production_default() -> None:
    validate_registry()
    assert not any(resource.production_default for resource in RESOURCE_REGISTRY)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"resource_id": "nasalseg"}, "resource_id values must be unique"),
        ({"roles": frozenset()}, "roles, modalities, and labels are required"),
        ({"modalities": frozenset()}, "roles, modalities, and labels are required"),
        ({"relevant_labels": ()}, "roles, modalities, and labels are required"),
        ({"source_url": "http://example.test"}, "source_url must use HTTPS"),
        ({"limitations": ()}, "at least one limitation is required"),
    ],
)
def test_validation_rejects_incomplete_or_duplicate_entries(
    change: dict[str, object], message: str
) -> None:
    original = get_resource("topcow")
    altered = replace(original, **change)
    resources = (get_resource("nasalseg"), altered) if change == {"resource_id": "nasalseg"} else (altered,)
    with pytest.raises(ValueError, match=message):
        validate_registry(resources)


@pytest.mark.parametrize(
    "resource_id",
    ["cntseg", "sinussegment"],
)
def test_validation_rejects_unavailable_production_default(resource_id: str) -> None:
    invalid = replace(get_resource(resource_id), production_default=True)
    with pytest.raises(ValueError, match="unavailable resource cannot be production default"):
        validate_registry((invalid,))


def test_validation_rejects_research_only_production_default() -> None:
    invalid = replace(get_resource("topcow"), production_default=True)
    with pytest.raises(ValueError, match="production default must be locally supported"):
        validate_registry((invalid,))
