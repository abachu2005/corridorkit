import hashlib
import importlib.util

import pytest

if importlib.util.find_spec("fastapi") is None:
    pytest.skip("cloud dependencies not installed", allow_module_level=True)

from fastapi.testclient import TestClient

from skullbase_corridor.cloud import service


def _payload(content=b"nifti"):
    import base64

    digest = hashlib.sha256(content).hexdigest()
    return {
        "cache_key": "a" * 64,
        "input": {
            "content_base64": base64.b64encode(content).decode(),
            "media_type": "application/x-nifti",
            "sha256": digest,
        },
        "model": {"name": "TotalSegmentator", "version": "2.11.0"},
        "parameters": {"tasks": ["craniofacial_structures"]},
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ENDPOINT_TOKEN", "test-token")
    monkeypatch.setenv("SKULLBASE_JOB_ROOT", str(tmp_path))
    monkeypatch.setattr(service.CpuWorker, "start", lambda self: None)
    monkeypatch.setattr(service.CpuWorker, "wake", lambda self: None)
    return TestClient(service.create_app())


def test_requires_authentication(client):
    assert client.post("/jobs", json=_payload()).status_code == 401


def test_submission_is_durable_and_idempotent(client):
    headers = {"Authorization": "Bearer test-token", "Idempotency-Key": "a" * 64}
    first = client.post("/jobs", json=_payload(), headers=headers)
    second = client.post("/jobs", json=_payload(), headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json() == second.json()
    status = client.get(
        f"/jobs/{first.json()['job_id']}",
        headers={"Authorization": "Bearer test-token"},
    )
    assert status.json()["state"] == "queued"


def test_rejects_bad_input_hash_and_idempotency_key(client):
    headers = {"Authorization": "Bearer test-token", "Idempotency-Key": "b" * 64}
    assert client.post("/jobs", json=_payload(), headers=headers).status_code == 400
    payload = _payload()
    payload["input"]["sha256"] = "0" * 64
    headers["Idempotency-Key"] = "a" * 64
    assert client.post("/jobs", json=payload, headers=headers).status_code == 400


def test_cancel_survives_subsequent_status_read(client):
    headers = {"Authorization": "Bearer test-token", "Idempotency-Key": "a" * 64}
    job = client.post("/jobs", json=_payload(), headers=headers).json()
    auth = {"Authorization": "Bearer test-token"}
    response = client.delete(f"/jobs/{job['job_id']}", headers=auth)
    assert response.json()["detail"] == "cancellation requested"
    assert client.get(f"/jobs/{job['job_id']}", headers=auth).json()["detail"] == (
        "cancellation requested"
    )
