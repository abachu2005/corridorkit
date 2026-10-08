"""Local TotalSegmentator execution for the public workstation workflow."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from skullbase_corridor.anatomy.azure_provider import (
    GeometryArtifact,
    InferenceProvenance,
    InferenceRequest,
    InferenceResult,
    JobState,
    JobStatus,
)


TASKS = (
    "craniofacial_structures",
    "head_glands_cavities",
    "headneck_bones_vessels",
)


async def run_local_inference(
    request: InferenceRequest,
    ct_path: Path,
    output: Path,
    *,
    device: str = "cpu",
    progress: Callable[[JobStatus], None] | None = None,
) -> InferenceResult:
    """Run pinned model tasks locally and return content-addressed artifacts.

    The caller owns ``output``. A complete result is reused by the higher-level
    content-hash cache; partial task directories are removed before execution.
    """
    executable = shutil.which("TotalSegmentator")
    if executable is None:
        raise RuntimeError(
            "TotalSegmentator is not installed in the managed runtime. "
            "Run the package setup/readiness command first."
        )
    if device not in {"cpu", "gpu", "mps"}:
        raise ValueError("local inference device must be cpu, gpu, or mps")
    output.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    job_id = f"local-{request.cache_key[:16]}"
    artifacts: list[GeometryArtifact] = []
    for task in TASKS:
        destination = output / task
        shutil.rmtree(destination, ignore_errors=True)
        if progress:
            progress(JobStatus(job_id, JobState.RUNNING, f"Local inference: {task}"))
        process = await asyncio.create_subprocess_exec(
            executable,
            "-i",
            str(ct_path),
            "-o",
            str(destination),
            "-ta",
            task,
            "-d",
            device,
        )
        try:
            returncode = await process.wait()
        except asyncio.CancelledError:
            process.terminate()
            await process.wait()
            raise
        if returncode:
            raise RuntimeError(f"TotalSegmentator task {task!r} exited {returncode}")
    for path in sorted(output.rglob("*.nii.gz")):
        content = path.read_bytes()
        artifacts.append(
            GeometryArtifact(
                name=path.name.removesuffix(".nii.gz"),
                kind="voxel_mask",
                uri=str(path.resolve()),
                sha256=hashlib.sha256(content).hexdigest(),
                coordinate_frame="RAS",
                affine=None,
                media_type="application/x-nifti",
            )
        )
    if not artifacts:
        raise RuntimeError("Local TotalSegmentator produced no NIfTI masks")
    return InferenceResult(
        geometries=tuple(artifacts),
        provenance=InferenceProvenance(
            provider=f"local-totalsegmentator-{device}",
            model_name=request.model_name,
            model_version=request.model_version,
            input_sha256=request.input_sha256,
            cache_key=request.cache_key,
            job_id=job_id,
            created_at=started,
            completed_at=datetime.now(timezone.utc),
        ),
        warnings=(
            "Inference ran locally; predicted anatomy requires review and is not a safety claim.",
        ),
    )
