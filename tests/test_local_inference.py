import asyncio
import hashlib
from pathlib import Path

from corridorkit.anatomy import local_inference
from corridorkit.anatomy.azure_provider import InferenceRequest


def test_local_inference_runs_each_task_and_hashes_artifacts(tmp_path, monkeypatch):
    executable = tmp_path / "TotalSegmentator"
    executable.write_text("# test executable\n")
    monkeypatch.setattr(local_inference.shutil, "which", lambda _: str(executable))
    calls = []

    class Process:
        async def wait(self):
            destination = Path(calls[-1][calls[-1].index("-o") + 1])
            destination.mkdir(parents=True)
            (destination / "mask.nii.gz").write_bytes(destination.name.encode())
            return 0

        def terminate(self):
            pass

    async def create(*args):
        calls.append(args)
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    request = InferenceRequest(
        content=b"ct",
        media_type="application/x-nifti",
        model_name="TotalSegmentator",
        model_version="2.11.0",
        parameters={"tasks": list(local_inference.TASKS)},
    )
    ct = tmp_path / "ct.nii.gz"
    ct.write_bytes(b"ct")
    result = asyncio.run(
        local_inference.run_local_inference(request, ct, tmp_path / "outputs")
    )

    assert len(calls) == len(local_inference.TASKS)
    assert result.provenance.provider == "local-totalsegmentator-cpu"
    assert result.provenance.input_sha256 == hashlib.sha256(b"ct").hexdigest()
    assert len(result.geometries) == len(local_inference.TASKS)
    assert all(Path(item.uri).is_file() for item in result.geometries)


def test_local_inference_requires_installed_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(local_inference.shutil, "which", lambda _: None)
    request = InferenceRequest(
        content=b"ct",
        media_type="application/x-nifti",
        model_name="TotalSegmentator",
        model_version="2.11.0",
    )
    try:
        asyncio.run(
            local_inference.run_local_inference(
                request, tmp_path / "ct.nii.gz", tmp_path / "outputs"
            )
        )
    except RuntimeError as error:
        assert "managed runtime" in str(error)
    else:
        raise AssertionError("missing TotalSegmentator was accepted")
