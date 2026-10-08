"""Asynchronous contracts for optional Azure-hosted anatomy inference.

The module performs no Azure provisioning and has no import-time network side
effects.  The HTTP contract is intentionally small so an endpoint can be
implemented independently and tested without Azure.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from types import MappingProxyType
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AzureProviderError(RuntimeError):
    """Base error for remote-provider failures."""


class AzureConfigurationError(AzureProviderError):
    """Raised when endpoint configuration is absent or unsafe."""


class AzureProtocolError(AzureProviderError):
    """Raised when an endpoint response violates the provider contract."""


class JobNotReadyError(AzureProviderError):
    """Raised when a result is requested before a job succeeds."""


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.SUCCEEDED, self.FAILED, self.CANCELLED}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class GeometryArtifact(_StrictModel):
    """A content-addressed geometry produced by inference."""

    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_frame: str = Field(pattern=r"^(RAS|LPS)$")
    affine: tuple[tuple[float, float, float, float], ...] | None = None
    media_type: str = Field(min_length=1)

    @field_validator("affine")
    @classmethod
    def validate_affine(cls, value):
        if value is None:
            return value
        if len(value) != 4 or any(len(row) != 4 for row in value):
            raise ValueError("affine must be 4x4")
        if tuple(value[3]) != (0.0, 0.0, 0.0, 1.0):
            raise ValueError("affine must have a homogeneous final row")
        return value


class InferenceProvenance(_StrictModel):
    """Traceability fields required for a remotely generated result."""

    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    job_id: str = Field(min_length=1)
    endpoint_request_id: str | None = None
    created_at: datetime
    completed_at: datetime


class InferenceResult(_StrictModel):
    """Geometry and provenance returned by a successful job."""

    schema_version: str = "1.0"
    geometries: tuple[GeometryArtifact, ...]
    provenance: InferenceProvenance
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    """Input bytes and model controls for one inference operation."""

    content: bytes = field(repr=False)
    media_type: str
    model_name: str
    model_version: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("content must not be empty")
        if not self.media_type or not self.model_name or not self.model_version:
            raise ValueError("media type and model identifiers are required")
        # Fail early if values cannot be represented deterministically.
        snapshot = json.loads(_canonical_json(dict(self.parameters)))
        object.__setattr__(self, "parameters", _freeze_json(snapshot))

    @property
    def input_sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def cache_key(self) -> str:
        return content_hash_cache_key(self)


@dataclass(frozen=True, slots=True)
class JobHandle:
    job_id: str
    cache_key: str


@dataclass(frozen=True, slots=True)
class JobStatus:
    job_id: str
    state: JobState
    detail: str | None = None


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("parameters must be finite JSON values") from exc


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def content_hash_cache_key(request: InferenceRequest) -> str:
    """Hash all inference-affecting content using an unambiguous envelope."""

    envelope = _canonical_json(
        {
            "input_sha256": request.input_sha256,
            "media_type": request.media_type,
            "model_name": request.model_name,
            "model_version": request.model_version,
            "parameters": dict(request.parameters),
        }
    )
    # Preserve the protocol namespace across the product rename so historical
    # content hashes and explicitly reused caches still identify the same input.
    return hashlib.sha256(b"skullbase-corridor-inference-v1\0" + envelope).hexdigest()


class AsyncInferenceProvider(Protocol):
    async def submit(self, request: InferenceRequest) -> JobHandle: ...

    async def status(self, job: JobHandle) -> JobStatus: ...

    async def cancel(self, job: JobHandle) -> JobStatus: ...

    async def result(self, job: JobHandle) -> InferenceResult: ...


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class AsyncHttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class UrllibAsyncTransport:
    """Small stdlib transport; blocking I/O is isolated from the event loop."""

    async def request(self, method, url, *, headers, body, timeout_seconds):
        def send() -> HttpResponse:
            request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
            try:
                with urllib.request.urlopen(  # noqa: S310 - URL is validated by provider
                    request,
                    timeout=timeout_seconds,
                    context=ssl.create_default_context(),
                ) as response:
                    return HttpResponse(
                        response.status,
                        dict(response.headers.items()),
                        response.read(),
                    )
            except urllib.error.HTTPError as exc:
                return HttpResponse(exc.code, dict(exc.headers.items()), exc.read())

        return await asyncio.wait_for(
            asyncio.to_thread(send),
            timeout=timeout_seconds + 0.5,
        )


class AzureMLHttpProvider:
    """Client for an asynchronous Azure ML-compatible HTTP job endpoint.

    The endpoint contract is ``POST /jobs``, ``GET /jobs/{id}``,
    ``DELETE /jobs/{id}``, and ``GET /jobs/{id}/result``.  Submission sends an
    ``Idempotency-Key`` header equal to the content-derived cache key.
    """

    endpoint_env = "AZURE_ML_ENDPOINT_URL"
    token_env = "AZURE_ML_ENDPOINT_TOKEN"

    def __init__(
        self,
        endpoint_url: str,
        token: str,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        retry_base_seconds: float = 0.25,
        transport: AsyncHttpTransport | None = None,
    ) -> None:
        parsed = urllib.parse.urlparse(endpoint_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise AzureConfigurationError("Azure endpoint must be an HTTPS URL without userinfo")
        if not token:
            raise AzureConfigurationError("Azure endpoint token is required")
        if timeout_seconds <= 0 or max_retries < 0 or retry_base_seconds < 0:
            raise ValueError("invalid timeout or retry configuration")
        self._endpoint_url = endpoint_url.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_base_seconds = retry_base_seconds
        self._transport = transport or UrllibAsyncTransport()

    @classmethod
    def from_env(cls, **kwargs) -> AzureMLHttpProvider:
        endpoint = os.environ.get(cls.endpoint_env, "")
        token = os.environ.get(cls.token_env, "")
        if not endpoint or not token:
            raise AzureConfigurationError(
                f"{cls.endpoint_env} and {cls.token_env} must both be set"
            )
        return cls(endpoint, token, **kwargs)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(endpoint_url={self._endpoint_url!r}, "
            "token=<redacted>)"
        )

    async def submit(self, request: InferenceRequest) -> JobHandle:
        payload = {
            "cache_key": request.cache_key,
            "input": {
                "content_base64": base64.b64encode(request.content).decode("ascii"),
                "media_type": request.media_type,
                "sha256": request.input_sha256,
            },
            "model": {"name": request.model_name, "version": request.model_version},
            "parameters": dict(request.parameters),
        }
        response = await self._json_request(
            "POST",
            "/jobs",
            payload,
            idempotency_key=request.cache_key,
        )
        job_id = response.get("job_id")
        cache_key = response.get("cache_key", request.cache_key)
        if not isinstance(job_id, str) or not job_id or cache_key != request.cache_key:
            raise AzureProtocolError("invalid job submission response")
        return JobHandle(job_id, cache_key)

    async def status(self, job: JobHandle) -> JobStatus:
        response = await self._json_request("GET", self._job_path(job))
        return self._parse_status(job, response)

    async def cancel(self, job: JobHandle) -> JobStatus:
        response = await self._json_request(
            "DELETE",
            self._job_path(job),
            idempotency_key=job.cache_key,
        )
        return self._parse_status(job, response)

    async def result(self, job: JobHandle) -> InferenceResult:
        response = await self._json_request("GET", f"{self._job_path(job)}/result")
        try:
            result = InferenceResult.model_validate(response)
        except Exception as exc:
            raise AzureProtocolError("invalid inference result response") from exc
        if (
            result.provenance.job_id != job.job_id
            or result.provenance.cache_key != job.cache_key
        ):
            raise AzureProtocolError("result provenance does not match requested job")
        return result

    async def artifact(self, geometry: GeometryArtifact) -> bytes:
        """Download and verify one same-origin artifact from a result."""

        parsed = urllib.parse.urlparse(geometry.uri)
        if parsed.scheme or parsed.netloc or not geometry.uri.startswith("/jobs/"):
            raise AzureProtocolError("artifact URI must be a same-origin /jobs path")
        response = await self._request("GET", geometry.uri)
        if hashlib.sha256(response.body).hexdigest() != geometry.sha256:
            raise AzureProtocolError(f"artifact SHA-256 mismatch for {geometry.name}")
        return response.body

    def _job_path(self, job: JobHandle) -> str:
        safe_id = urllib.parse.quote(job.job_id, safe="")
        return f"/jobs/{safe_id}"

    def _parse_status(self, job: JobHandle, response: Mapping[str, Any]) -> JobStatus:
        try:
            if response.get("job_id") != job.job_id:
                raise ValueError
            return JobStatus(
                job.job_id,
                JobState(response["state"]),
                response.get("detail"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AzureProtocolError("invalid job status response") from exc

    async def _json_request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> Mapping[str, Any]:
        response = await self._request(
            method,
            path,
            _canonical_json(payload) if payload is not None else None,
            idempotency_key=idempotency_key,
        )
        try:
            decoded = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AzureProtocolError("endpoint returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise AzureProtocolError("endpoint JSON must be an object")
        return decoded

    async def _request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> HttpResponse:
        headers = {
            "Accept": "*/*",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "corridorkit/azure-provider",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._transport.request(
                    method,
                    f"{self._endpoint_url}{path}",
                    headers=headers,
                    body=body,
                    timeout_seconds=self._timeout_seconds,
                )
            except (TimeoutError, asyncio.TimeoutError, OSError) as exc:
                if attempt >= self._max_retries:
                    raise AzureProviderError("Azure request failed after retries") from exc
            else:
                if 200 <= response.status < 300:
                    return response
                if response.status not in {408, 425, 429} and response.status < 500:
                    raise AzureProviderError(f"Azure endpoint returned HTTP {response.status}")
                if attempt >= self._max_retries:
                    raise AzureProviderError(
                        f"Azure endpoint returned HTTP {response.status} after retries"
                    )
            delay = self._retry_base_seconds * (2**attempt)
            if delay:
                await asyncio.sleep(delay + random.uniform(0, delay * 0.1))
        raise AssertionError("retry loop exhausted")


@dataclass(slots=True)
class _FakeJob:
    request: InferenceRequest
    state: JobState
    result: InferenceResult


class DeterministicFakeProvider:
    """Offline provider with stable IDs and caller-supplied deterministic geometry."""

    def __init__(self, geometries: tuple[GeometryArtifact, ...] = ()) -> None:
        self._geometries = geometries
        self._jobs: dict[str, _FakeJob] = {}

    async def submit(self, request: InferenceRequest) -> JobHandle:
        job_id = f"fake-{request.cache_key[:24]}"
        if job_id not in self._jobs:
            # A fixed timestamp keeps complete fake results reproducible across
            # processes; it is deliberately not presented as observed runtime.
            now = datetime(2000, 1, 1, tzinfo=timezone.utc)
            result = InferenceResult(
                geometries=self._geometries,
                provenance=InferenceProvenance(
                    provider="deterministic-fake",
                    model_name=request.model_name,
                    model_version=request.model_version,
                    input_sha256=request.input_sha256,
                    cache_key=request.cache_key,
                    job_id=job_id,
                    created_at=now,
                    completed_at=now,
                ),
                warnings=("Synthetic provider output; not a clinical inference.",),
            )
            self._jobs[job_id] = _FakeJob(request, JobState.SUCCEEDED, result)
        return JobHandle(job_id, request.cache_key)

    async def status(self, job: JobHandle) -> JobStatus:
        record = self._get(job)
        return JobStatus(job.job_id, record.state)

    async def cancel(self, job: JobHandle) -> JobStatus:
        record = self._get(job)
        if not record.state.terminal:
            record.state = JobState.CANCELLED
        return JobStatus(job.job_id, record.state)

    async def result(self, job: JobHandle) -> InferenceResult:
        record = self._get(job)
        if record.state != JobState.SUCCEEDED:
            raise JobNotReadyError(f"job is {record.state}")
        return record.result

    def _get(self, job: JobHandle) -> _FakeJob:
        record = self._jobs.get(job.job_id)
        if record is None or record.request.cache_key != job.cache_key:
            raise AzureProviderError("unknown job")
        return record


class FilesystemInferenceCache:
    """Durable verified result and artifact cache keyed by immutable inference hash."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def get(self, cache_key: str) -> InferenceResult | None:
        path = self._path(cache_key)
        if not path.is_file():
            return None
        try:
            result = InferenceResult.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise AzureProtocolError(f"invalid cached inference result: {path}") from exc
        if result.provenance.cache_key != cache_key:
            raise AzureProtocolError("cached result provenance does not match its cache key")
        return result

    def put(self, result: InferenceResult) -> Path:
        cache_key = result.provenance.cache_key
        path = self._path(cache_key)
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{cache_key}.", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(result.model_dump_json(indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return path

    def artifact_path(self, cache_key: str, artifact: GeometryArtifact) -> Path:
        safe_name = hashlib.sha256(
            f"{artifact.name}\0{artifact.sha256}".encode("utf-8")
        ).hexdigest()
        return self.directory / cache_key / f"{safe_name}.bin"

    def get_artifact(self, cache_key: str, artifact: GeometryArtifact) -> bytes | None:
        path = self.artifact_path(cache_key, artifact)
        if not path.is_file():
            return None
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise AzureProtocolError(f"invalid cached artifact: {path}")
        return content

    def put_artifact(
        self, cache_key: str, artifact: GeometryArtifact, content: bytes
    ) -> Path:
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise AzureProtocolError(f"artifact SHA-256 mismatch for {artifact.name}")
        path = self.artifact_path(cache_key, artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return path

    def _path(self, cache_key: str) -> Path:
        if len(cache_key) != 64 or any(character not in "0123456789abcdef" for character in cache_key):
            raise ValueError("cache key must be a lowercase SHA-256 digest")
        return self.directory / f"{cache_key}.json"


async def wait_for_inference(
    provider: AsyncInferenceProvider,
    request: InferenceRequest,
    *,
    cache: FilesystemInferenceCache | None = None,
    timeout_seconds: float = 900.0,
    poll_seconds: float = 1.0,
    progress: Any | None = None,
) -> InferenceResult:
    """Submit and await one bounded job, reusing a verified durable result.

    Cancellation of this coroutine requests remote cancellation before the
    cancellation is propagated. ``progress`` receives each :class:`JobStatus`.
    """

    if timeout_seconds <= 0 or poll_seconds <= 0:
        raise ValueError("timeout_seconds and poll_seconds must be positive")
    if cache is not None:
        cached = cache.get(request.cache_key)
        if cached is not None:
            return cached
    job = await provider.submit(request)

    async def poll() -> InferenceResult:
        while True:
            status = await provider.status(job)
            if progress is not None:
                progress(status)
            if status.state is JobState.SUCCEEDED:
                result = await provider.result(job)
                if result.provenance.input_sha256 != request.input_sha256:
                    raise AzureProtocolError("result provenance does not match submitted input")
                if cache is not None:
                    cache.put(result)
                return result
            if status.state in {JobState.FAILED, JobState.CANCELLED}:
                raise AzureProviderError(
                    f"inference job {status.state.value}: {status.detail or 'no detail'}"
                )
            await asyncio.sleep(poll_seconds)

    try:
        return await asyncio.wait_for(poll(), timeout=timeout_seconds)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        await provider.cancel(job)
        raise
