"""Durable, authenticated CPU inference gateway.

One process serves the HTTP contract and a bounded single-worker queue. Job
records and artifacts live below ``CORRIDORKIT_JOB_ROOT``; production mounts an
Azure Files share there so restarts do not lose idempotency or results.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from corridorkit.anatomy.azure_provider import (
    GeometryArtifact,
    InferenceProvenance,
    InferenceResult,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputEnvelope(_StrictModel):
    content_base64: str
    media_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ModelEnvelope(_StrictModel):
    name: str
    version: str


class SubmitEnvelope(_StrictModel):
    cache_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    input: InputEnvelope
    model: ModelEnvelope
    parameters: dict = Field(default_factory=dict)


class JobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.jobs = self.root / "jobs"
        self._lock = threading.Lock()
        self.jobs.mkdir(parents=True, exist_ok=True)

    def directory(self, job_id: str) -> Path:
        if not job_id or any(value not in "0123456789abcdef-" for value in job_id):
            raise KeyError(job_id)
        directory = (self.jobs / job_id).resolve()
        if self.jobs.resolve() not in directory.parents:
            raise KeyError(job_id)
        return directory

    def read(self, job_id: str) -> dict:
        try:
            return json.loads((self.directory(job_id) / "job.json").read_text())
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            raise KeyError(job_id) from exc

    def write(self, record: dict) -> None:
        directory = self.directory(record["job_id"])
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".job-{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
        os.replace(temporary, directory / "job.json")

    def submit(self, payload: SubmitEnvelope) -> tuple[dict, bool]:
        with self._lock:
            for descriptor in self.jobs.glob("*/job.json"):
                try:
                    existing = json.loads(descriptor.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                if existing.get("cache_key") == payload.cache_key:
                    return existing, False
            content = base64.b64decode(payload.input.content_base64, validate=True)
            if hashlib.sha256(content).hexdigest() != payload.input.sha256:
                raise ValueError("input SHA-256 mismatch")
            job_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            record = {
                "job_id": job_id,
                "cache_key": payload.cache_key,
                "state": "queued",
                "detail": None,
                "created_at": now,
                "completed_at": None,
                "input_sha256": payload.input.sha256,
                "media_type": payload.input.media_type,
                "model_name": payload.model.name,
                "model_version": payload.model.version,
                "parameters": payload.parameters,
                "cancel_requested": False,
            }
            directory = self.directory(job_id)
            directory.mkdir(parents=True)
            (directory / "input.nii.gz").write_bytes(content)
            self.write(record)
            return record, True


class CpuWorker:
    def __init__(self, store: JobStore):
        self.store = store
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._process: subprocess.Popen | None = None
        self._active_id: str | None = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name="cpu-inference")

    def start(self):
        self._thread.start()

    def wake(self):
        self._wake.set()

    def cancel(self, job_id: str):
        if self._active_id == job_id and self._process is not None:
            self._process.terminate()
        self._wake.set()

    def _loop(self):
        while not self._stop.is_set():
            queued = []
            for descriptor in self.store.jobs.glob("*/job.json"):
                try:
                    record = json.loads(descriptor.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                if record["state"] in {"queued", "running"}:
                    queued.append(record)
            if not queued:
                self._wake.wait(2)
                self._wake.clear()
                continue
            record = sorted(queued, key=lambda item: item["created_at"])[0]
            self._run(record)

    def _run(self, record: dict):
        job_id = record["job_id"]
        if record.get("cancel_requested"):
            record.update(state="cancelled", detail="cancelled before execution")
            self.store.write(record)
            return
        directory = self.store.directory(job_id)
        output = directory / "outputs"
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir()
        record.update(state="running", detail="CPU anatomy inference")
        self.store.write(record)
        self._active_id = job_id
        tasks = tuple(record["parameters"].get("tasks", (
            "craniofacial_structures",
            "head_glands_cavities",
            "headneck_bones_vessels",
        )))
        try:
            for task in tasks:
                latest = self.store.read(job_id)
                if latest.get("cancel_requested"):
                    raise InterruptedError("cancel requested")
                destination = output / task
                command = [
                    "TotalSegmentator", "-i", str(directory / "input.nii.gz"),
                    "-o", str(destination), "-ta", task, "-d", "cpu",
                ]
                self._process = subprocess.Popen(command)
                deadline = time.monotonic() + float(os.getenv("CORRIDORKIT_JOB_TIMEOUT_SECONDS", "4800"))
                while self._process.poll() is None:
                    if time.monotonic() > deadline:
                        self._process.terminate()
                        raise TimeoutError(f"{task} exceeded worker deadline")
                    if self.store.read(job_id).get("cancel_requested"):
                        self._process.terminate()
                        raise InterruptedError("cancel requested")
                    time.sleep(0.5)
                if self._process.returncode:
                    raise RuntimeError(f"{task} exited {self._process.returncode}")
            artifacts = []
            for path in sorted(output.rglob("*.nii.gz")):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                relative = path.relative_to(directory)
                artifacts.append({
                    "name": path.name.removesuffix(".nii.gz"),
                    "kind": "voxel_mask",
                    "uri": f"/jobs/{job_id}/artifacts/{relative.as_posix()}",
                    "sha256": digest,
                    "coordinate_frame": "RAS",
                    "affine": None,
                    "media_type": "application/x-nifti",
                })
            now = datetime.now(timezone.utc)
            result = InferenceResult(
                geometries=tuple(GeometryArtifact.model_validate(item) for item in artifacts),
                provenance=InferenceProvenance(
                    provider="azure-container-apps-cpu",
                    model_name=record["model_name"],
                    model_version=record["model_version"],
                    input_sha256=record["input_sha256"],
                    cache_key=record["cache_key"],
                    job_id=job_id,
                    created_at=datetime.fromisoformat(record["created_at"]),
                    completed_at=now,
                ),
                warnings=("Predicted anatomy requires review; model feasibility is not safety.",),
            )
            (directory / "result.json").write_text(result.model_dump_json(indent=2) + "\n")
            record.update(state="succeeded", detail=None, completed_at=now.isoformat())
        except InterruptedError as exc:
            record.update(
                state="cancelled", detail=str(exc),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            record.update(
                state="failed", detail=f"{type(exc).__name__}: {exc}",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
        finally:
            self._process = None
            self._active_id = None
            self.store.write(record)


def create_app() -> FastAPI:
    token = os.environ.get("ENDPOINT_TOKEN", "")
    if not token:
        raise RuntimeError("ENDPOINT_TOKEN is required")
    store = JobStore(os.environ.get("CORRIDORKIT_JOB_ROOT", "/data"))
    worker = CpuWorker(store)
    app = FastAPI(title="CorridorKit CPU Inference", docs_url=None, redoc_url=None)

    def authorize(authorization: str | None):
        import hmac
        supplied = authorization.removeprefix("Bearer ") if authorization else ""
        if not hmac.compare_digest(supplied, token):
            raise HTTPException(401, "unauthorized")

    @app.on_event("startup")
    def startup():
        worker.start()
        worker.wake()

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.post("/jobs", status_code=202)
    async def submit(
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ):
        authorize(authorization)
        payload = SubmitEnvelope.model_validate(await request.json())
        if idempotency_key != payload.cache_key:
            raise HTTPException(400, "Idempotency-Key must equal cache_key")
        try:
            record, _ = store.submit(payload)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        worker.wake()
        return {"job_id": record["job_id"], "cache_key": record["cache_key"]}

    @app.get("/jobs/{job_id}")
    def status(job_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try:
            record = store.read(job_id)
        except KeyError as exc:
            raise HTTPException(404, "job not found") from exc
        return {key: record[key] for key in ("job_id", "state", "detail")}

    @app.delete("/jobs/{job_id}")
    def cancel(job_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try:
            record = store.read(job_id)
        except KeyError as exc:
            raise HTTPException(404, "job not found") from exc
        if record["state"] not in {"succeeded", "failed", "cancelled"}:
            record["cancel_requested"] = True
            record["detail"] = "cancellation requested"
            store.write(record)
            worker.cancel(job_id)
        return {key: record[key] for key in ("job_id", "state", "detail")}

    @app.get("/jobs/{job_id}/result")
    def result(job_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try:
            record = store.read(job_id)
        except KeyError as exc:
            raise HTTPException(404, "job not found") from exc
        if record["state"] != "succeeded":
            raise HTTPException(409, f"job is {record['state']}")
        return json.loads((store.directory(job_id) / "result.json").read_text())

    @app.get("/jobs/{job_id}/artifacts/{artifact_path:path}")
    def artifact(
        job_id: str,
        artifact_path: str,
        authorization: str | None = Header(default=None),
    ):
        authorize(authorization)
        root = store.directory(job_id)
        path = (root / artifact_path).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "artifact not found")
        return FileResponse(path, media_type="application/x-nifti")

    return app
