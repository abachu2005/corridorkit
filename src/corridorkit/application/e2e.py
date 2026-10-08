"""Single-request CT-to-reviewed-corridor workflow used by the Slicer module."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import tempfile
from pathlib import Path

import numpy as np

from corridorkit.anatomy.azure_provider import (
    AzureMLHttpProvider,
    FilesystemInferenceCache,
    InferenceRequest,
    wait_for_inference,
)
from corridorkit.anatomy.entry_proposals import propose_entry_candidates
from corridorkit.anatomy.local_inference import run_local_inference
from corridorkit.anatomy.totalsegmentator_adapter import adapt_outputs
from corridorkit.domain.models import (
    ApproachConfig,
    ApproachKind,
    CorridorCase,
    ExactPathCandidate,
    KnowledgeStatus,
    PortalDisk,
    ProtectedStructure,
    RigidInstrument,
    SamplingConfig,
    TargetPointCloud,
    VoxelGeometry,
)
from corridorkit.geometry.engine import evaluate_exact_paths
from corridorkit.io.volumes import load_volume


UNKNOWN_CRITICAL = (
    "cranial nerves not represented by available CT models",
    "cavernous sinus contents",
    "ophthalmic arteries",
    "dura",
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


async def run_workflow(request_path: str | Path, output_path: str | Path) -> Path:
    request_data = json.loads(Path(request_path).read_text())
    output = Path(output_path)
    work = output.parent / (output.stem + "-artifacts")
    work.mkdir(parents=True, exist_ok=True)
    ct_path = Path(request_data["ct_path"])
    ct = load_volume(ct_path, assume_spatial_unit="mm")
    target = tuple(float(item) for item in request_data["target_ras_mm"])
    instrument = request_data.get("instrument", {})

    inference_request = InferenceRequest(
        content=ct_path.read_bytes(),
        media_type="application/x-nifti",
        model_name="TotalSegmentator",
        model_version="2.11.0",
        parameters={
            "tasks": [
                "craniofacial_structures",
                "head_glands_cavities",
                "headneck_bones_vessels",
            ]
        },
    )
    backend = str(request_data.get("inference_backend", "local"))
    cache = FilesystemInferenceCache(
        request_data.get("cache_directory", str(Path.home() / ".corridorkit/cache"))
    )
    stages = []

    def progress(status):
        stages.append({"state": status.state.value, "detail": status.detail})
        _write_json(output.with_suffix(".progress.json"), stages[-1])

    result = cache.get(inference_request.cache_key)
    provider = None
    if result is None:
        if backend == "local":
            result = await run_local_inference(
                inference_request,
                ct_path,
                work / "local-model-outputs",
                device=str(request_data.get("local_device", "cpu")),
                progress=progress,
            )
            cache.put(result)
        elif backend == "azure":
            provider = AzureMLHttpProvider.from_env(timeout_seconds=120, max_retries=2)
            result = await wait_for_inference(
                provider,
                inference_request,
                cache=cache,
                timeout_seconds=float(request_data.get("timeout_seconds", 5400)),
                poll_seconds=1,
                progress=progress,
            )
        else:
            raise ValueError("inference_backend must be 'local' or 'azure'")
    masks_root = work / "model-outputs"
    for geometry in result.geometries:
        relative = geometry.uri.split("/artifacts/", 1)[-1]
        destination = masks_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = cache.get_artifact(inference_request.cache_key, geometry)
        if content is None:
            if backend == "local":
                source = Path(geometry.uri)
                content = source.read_bytes()
            else:
                assert provider is not None
                content = await provider.artifact(geometry)
            cache.put_artifact(inference_request.cache_key, geometry, content)
        if hashlib.sha256(content).hexdigest() != geometry.sha256:
            raise RuntimeError("artifact verification failed")
        destination.write_bytes(content)

    adapted = adapt_outputs(masks_root, ct, model_version=result.provenance.model_version)
    proposal = propose_entry_candidates(
        target,
        adapted.masks,
        ct.affine,
        candidates_per_surface=int(request_data.get("candidates_per_surface", 2)),
    )
    mask_files = {}
    for name, item in adapted.masks.items():
        path = work / f"{name}.npy"
        np.save(path, np.asarray(item.data, dtype=np.uint8), allow_pickle=False)
        mask_files[name] = path

    structures = [
        ProtectedStructure(name=name, status=KnowledgeStatus.UNKNOWN)
        for name in UNKNOWN_CRITICAL
    ]
    affine = tuple(tuple(float(value) for value in row) for row in ct.affine)
    if "bone" in mask_files:
        structures.append(ProtectedStructure(
            name="predicted bone",
            geometry=VoxelGeometry(uri=str(mask_files["bone"]), affine=affine),
            tissue_type="bone",
        ))
    if "protected" in mask_files:
        structures.append(ProtectedStructure(
            name="predicted available protected anatomy",
            geometry=VoxelGeometry(uri=str(mask_files["protected"]), affine=affine),
        ))
    if "left_ica" not in adapted.masks:
        structures.append(ProtectedStructure(
            name="left internal carotid artery", status=KnowledgeStatus.UNKNOWN))
    if "right_ica" not in adapted.masks:
        structures.append(ProtectedStructure(
            name="right internal carotid artery", status=KnowledgeStatus.UNKNOWN))

    approaches = []
    exact_candidates = []
    shaft_radius = float(instrument.get("shaft_diameter_mm", 2.0)) / 2
    portal_radius = float(instrument.get("portal_diameter_mm", 8.0)) / 2
    length = float(instrument.get("length_mm", 200.0))
    for candidate in proposal.candidates:
        approaches.append(ApproachConfig(
            name=candidate.candidate_id,
            kind=(ApproachKind.EEA if candidate.approach == "eea"
                  else ApproachKind.TRANSMAXILLARY),
            portal=PortalDisk(
                center_mm=candidate.entry_ras_mm,
                normal=candidate.direction,
                radius_mm=portal_radius,
            ),
            nominal_direction=candidate.direction,
            instrument=RigidInstrument(length_mm=length, radius_mm=shaft_radius),
            sampling=SamplingConfig(
                max_angle_deg=1, polar_steps=1, azimuth_steps=1,
                target_directed=False, adaptive_levels=0,
            ),
            target_tolerance_mm=0,
            allow_unknown_anatomy=False,
        ))
        exact_candidates.append(ExactPathCandidate(
            candidate_id=candidate.candidate_id,
            approach_name=candidate.candidate_id,
            entry_point_mm=candidate.entry_ras_mm,
            target_point_mm=target,
        ))
    exact = []
    if approaches:
        case = CorridorCase(
            case_id=f"integrated-{inference_request.input_sha256[:12]}",
            target=TargetPointCloud(points_mm=[target], source="Slicer target markup"),
            protected_structures=structures,
            approaches=approaches,
        )
        exact = evaluate_exact_paths(case, exact_candidates, base_directory=work)

    payload = {
        "schema_version": "integrated-e2e-v1",
        "safety_claim": False,
        "input_sha256": inference_request.input_sha256,
        "inference": result.model_dump(mode="json"),
        "anatomy": {
            "model": adapted.model_name,
            "version": adapted.model_version,
            "tasks": adapted.tasks,
            "missing": adapted.missing,
            "warnings": adapted.warnings,
            "mask_files": {name: str(path) for name, path in mask_files.items()},
            "sources": adapted.sources,
        },
        "proposal": proposal.model_dump(mode="json"),
        "exact_paths": [item.model_dump(mode="json") for item in exact],
        "interpretation": (
            "model_feasible means feasible under represented masks only; "
            "blocked/unavailable/conditional are retained exactly and no state means safe"
        ),
    }
    _write_json(output, payload)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    async def cancellable_run():
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(run_workflow(args.request, args.output))
        installed = []
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, task.cancel)
                installed.append(signum)
            except (NotImplementedError, RuntimeError):
                pass
        try:
            await task
        finally:
            for signum in installed:
                loop.remove_signal_handler(signum)

    asyncio.run(cancellable_run())


if __name__ == "__main__":
    main()
