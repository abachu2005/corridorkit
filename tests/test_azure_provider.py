import asyncio
import json
from collections import deque

import pytest
from pydantic import ValidationError

from corridorkit.anatomy.azure_provider import (
    AzureConfigurationError,
    AzureMLHttpProvider,
    DeterministicFakeProvider,
    FilesystemInferenceCache,
    GeometryArtifact,
    HttpResponse,
    InferenceRequest,
    JobState,
    wait_for_inference,
)


def run(coroutine):
    return asyncio.run(coroutine)


def request(parameters=None):
    return InferenceRequest(
        content=b"offline-volume-bytes",
        media_type="application/x-nifti",
        model_name="test-model",
        model_version="sha256:locked",
        parameters=parameters or {"labels": ["bone", "air"]},
    )


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    async def request(self, method, url, *, headers, body, timeout_seconds):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        response = self.responses.popleft()
        if isinstance(response, BaseException):
            raise response
        return response


def response(payload, status=200):
    return HttpResponse(status, {}, json.dumps(payload).encode())


def valid_result(job_id, cache_key, input_sha256):
    return {
        "schema_version": "1.0",
        "geometries": [
            {
                "name": "bone",
                "kind": "voxel_mask",
                "uri": "azureml://outputs/bone.nii.gz",
                "sha256": "a" * 64,
                "coordinate_frame": "RAS",
                "affine": [
                    [1, 0, 0, 0],
                    [0, 1, 0, 0],
                    [0, 0, 1, 0],
                    [0, 0, 0, 1],
                ],
                "media_type": "application/x-nifti",
            }
        ],
        "provenance": {
            "provider": "azure-ml",
            "model_name": "test-model",
            "model_version": "sha256:locked",
            "input_sha256": input_sha256,
            "cache_key": cache_key,
            "job_id": job_id,
            "endpoint_request_id": "safe-request-id",
            "created_at": "2026-10-07T12:00:00Z",
            "completed_at": "2026-10-07T12:01:00Z",
        },
        "warnings": [],
    }


def test_cache_key_is_content_derived_canonical_and_model_scoped():
    first = request({"threshold": 0.5, "labels": ["bone"]})
    reordered = request({"labels": ["bone"], "threshold": 0.5})
    changed = InferenceRequest(
        content=first.content + b"x",
        media_type=first.media_type,
        model_name=first.model_name,
        model_version=first.model_version,
        parameters=first.parameters,
    )
    assert first.cache_key == reordered.cache_key
    assert len(first.cache_key) == 64
    assert first.cache_key != changed.cache_key


def test_request_parameters_are_deeply_immutable():
    parameters = {"labels": ["bone"]}
    submitted = request(parameters)
    parameters["labels"].append("air")
    assert submitted.parameters["labels"] == ("bone",)
    with pytest.raises(TypeError):
        submitted.parameters["other"] = True


def test_fake_provider_is_deterministic_and_idempotent():
    geometry = GeometryArtifact(
        name="synthetic",
        kind="voxel_mask",
        uri="memory://synthetic",
        sha256="b" * 64,
        coordinate_frame="RAS",
        media_type="application/x-nifti",
    )
    provider = DeterministicFakeProvider((geometry,))
    first = run(provider.submit(request()))
    second = run(provider.submit(request()))
    assert first == second
    assert run(provider.status(first)).state == JobState.SUCCEEDED
    result = run(provider.result(first))
    assert result.geometries == (geometry,)
    assert result.provenance.cache_key == first.cache_key
    assert "not a clinical inference" in result.warnings[0]


def test_http_provider_contract_and_no_token_in_repr():
    submitted = request()
    transport = ScriptedTransport(
        [
            response({"job_id": "job/one", "cache_key": submitted.cache_key}),
            response({"job_id": "job/one", "state": "running"}),
            response(valid_result("job/one", submitted.cache_key, submitted.input_sha256)),
            response({"job_id": "job/one", "state": "cancelled"}),
        ]
    )
    provider = AzureMLHttpProvider(
        "https://workspace.example.azureml.net/score",
        "super-secret",
        transport=transport,
        retry_base_seconds=0,
    )
    handle = run(provider.submit(submitted))
    assert run(provider.status(handle)).state == JobState.RUNNING
    assert run(provider.result(handle)).geometries[0].coordinate_frame == "RAS"
    assert run(provider.cancel(handle)).state == JobState.CANCELLED

    assert "super-secret" not in repr(provider)
    assert transport.calls[0]["headers"]["Idempotency-Key"] == submitted.cache_key
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer super-secret"
    assert transport.calls[1]["url"].endswith("/jobs/job%2Fone")
    assert transport.calls[2]["url"].endswith("/jobs/job%2Fone/result")
    payload = json.loads(transport.calls[0]["body"])
    assert payload["input"]["sha256"] == submitted.input_sha256


def test_artifact_download_is_same_origin_and_hash_verified():
    content = b"verified-mask"
    geometry = valid_result("job", "a" * 64, "b" * 64)["geometries"][0]
    geometry["uri"] = "/jobs/job/artifacts/outputs/bone.nii.gz"
    import hashlib
    geometry["sha256"] = hashlib.sha256(content).hexdigest()
    artifact = GeometryArtifact.model_validate(geometry)
    transport = ScriptedTransport([HttpResponse(200, {}, content)])
    provider = AzureMLHttpProvider(
        "https://example.azureml.net", "token", transport=transport,
        retry_base_seconds=0,
    )
    assert run(provider.artifact(artifact)) == content

    invalid = artifact.model_copy(update={"sha256": "0" * 64})
    transport.responses.append(HttpResponse(200, {}, content))
    with pytest.raises(Exception, match="SHA-256"):
        run(provider.artifact(invalid))


def test_submit_retries_timeout_with_same_idempotency_key():
    submitted = request()
    transport = ScriptedTransport(
        [
            TimeoutError("offline timeout"),
            response({"job_id": "retry-job", "cache_key": submitted.cache_key}),
        ]
    )
    provider = AzureMLHttpProvider(
        "https://example.azureml.net",
        "token",
        transport=transport,
        max_retries=1,
        retry_base_seconds=0,
    )
    assert run(provider.submit(submitted)).job_id == "retry-job"
    assert len(transport.calls) == 2
    assert {
        call["headers"]["Idempotency-Key"] for call in transport.calls
    } == {submitted.cache_key}


def test_configuration_requires_https_and_env_pair(monkeypatch):
    with pytest.raises(AzureConfigurationError):
        AzureMLHttpProvider("http://example.test", "token")
    monkeypatch.delenv("AZURE_ML_ENDPOINT_URL", raising=False)
    monkeypatch.delenv("AZURE_ML_ENDPOINT_TOKEN", raising=False)
    with pytest.raises(AzureConfigurationError):
        AzureMLHttpProvider.from_env()


def test_geometry_schema_rejects_unverifiable_artifact():
    with pytest.raises(ValidationError):
        GeometryArtifact(
            name="bad",
            kind="mask",
            uri="memory://bad",
            sha256="not-a-hash",
            coordinate_frame="unknown",
            media_type="application/octet-stream",
        )


def test_wait_for_inference_persists_and_reuses_verified_result(tmp_path):
    provider = DeterministicFakeProvider()
    cache = FilesystemInferenceCache(tmp_path)
    submitted = request()
    observed = []

    first = run(
        wait_for_inference(
            provider,
            submitted,
            cache=cache,
            poll_seconds=0.001,
            progress=observed.append,
        )
    )
    assert first.provenance.cache_key == submitted.cache_key
    assert observed[-1].state is JobState.SUCCEEDED
    cached = cache.get(submitted.cache_key)
    assert cached == first

    class MustNotSubmit:
        async def submit(self, unused):  # pragma: no cover - assertion path
            raise AssertionError("cache hit must not submit a second job")

    assert run(wait_for_inference(MustNotSubmit(), submitted, cache=cache)) == first


def test_filesystem_cache_rejects_invalid_keys(tmp_path):
    cache = FilesystemInferenceCache(tmp_path)
    with pytest.raises(ValueError, match="SHA-256"):
        cache.get("../escape")


def test_filesystem_cache_verifies_artifact_bytes(tmp_path):
    import hashlib

    content = b"mask-bytes"
    artifact = GeometryArtifact(
        name="bone",
        kind="voxel_mask",
        uri="/jobs/id/artifacts/bone.nii.gz",
        sha256=hashlib.sha256(content).hexdigest(),
        coordinate_frame="RAS",
        media_type="application/x-nifti",
    )
    cache = FilesystemInferenceCache(tmp_path)
    key = "a" * 64
    path = cache.put_artifact(key, artifact, content)
    assert cache.get_artifact(key, artifact) == content
    path.write_bytes(b"tampered")
    with pytest.raises(Exception, match="invalid cached artifact"):
        cache.get_artifact(key, artifact)
