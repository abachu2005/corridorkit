import asyncio
import hashlib
import json
from datetime import datetime, timezone

import nibabel as nib
import numpy as np

from skullbase_corridor.anatomy.azure_provider import (
    GeometryArtifact,
    InferenceProvenance,
    InferenceResult,
)
from skullbase_corridor.application import e2e


def _save(path, data, affine):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = nib.Nifti1Image(data.astype(np.uint8), affine)
    image.set_qform(affine, code=1)
    image.set_sform(affine, code=1)
    image.header.set_xyzt_units("mm")
    nib.save(image, path)


def test_integrated_workflow_uses_cloud_artifacts_and_exact_evaluation(tmp_path, monkeypatch):
    affine = np.eye(4)
    affine[:3, 3] = (-20, -10, -10)
    shape = (41, 31, 21)
    ct = tmp_path / "ct.nii.gz"
    _save(ct, np.zeros(shape), affine)
    model_outputs = tmp_path / "generated"
    nasal = np.zeros(shape)
    nasal[8:14, 3:15, 7:14] = nasal[27:33, 3:15, 7:14] = 1
    sinus = np.zeros(shape)
    sinus[3:11, 3:15, 6:15] = sinus[30:38, 3:15, 6:15] = 1
    bone = np.zeros(shape)
    bone[2:4, 3:15, 6:15] = bone[37:39, 3:15, 6:15] = 1
    files = {
        "head_glands_cavities/nasal_cavity.nii.gz": nasal,
        "craniofacial_structures/sinus_maxillary.nii.gz": sinus,
        "craniofacial_structures/skull.nii.gz": bone,
    }
    artifacts = []
    artifact_bytes = {}
    for relative, data in files.items():
        path = model_outputs / relative
        _save(path, data, affine)
        content = path.read_bytes()
        uri = f"/jobs/job/artifacts/outputs/{relative}"
        artifact_bytes[uri] = content
        artifacts.append(GeometryArtifact(
            name=path.name.removesuffix(".nii.gz"),
            kind="voxel_mask",
            uri=uri,
            sha256=hashlib.sha256(content).hexdigest(),
            coordinate_frame="RAS",
            media_type="application/x-nifti",
        ))

    class Provider:
        @classmethod
        def from_env(cls, **kwargs):
            return cls()

        async def artifact(self, geometry):
            return artifact_bytes[geometry.uri]

    now = datetime.now(timezone.utc)

    async def infer(provider, request, **kwargs):
        return InferenceResult(
            geometries=tuple(artifacts),
            provenance=InferenceProvenance(
                provider="test",
                model_name=request.model_name,
                model_version=request.model_version,
                input_sha256=request.input_sha256,
                cache_key=request.cache_key,
                job_id="job",
                created_at=now,
                completed_at=now,
            ),
        )

    monkeypatch.setattr(e2e, "AzureMLHttpProvider", Provider)
    monkeypatch.setattr(e2e, "wait_for_inference", infer)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({
        "ct_path": str(ct),
        "target_ras_mm": [10, 20, 0],
        "cache_directory": str(tmp_path / "cache"),
        "candidates_per_surface": 1,
        "inference_backend": "azure",
    }))
    output = tmp_path / "result.json"

    asyncio.run(e2e.run_workflow(request, output))
    result = json.loads(output.read_text())

    assert result["schema_version"] == "integrated-e2e-v1"
    assert result["proposal"]["candidates"]
    assert len(result["exact_paths"]) == len(result["proposal"]["candidates"])
    assert all(item["state"] in {"blocked", "unavailable"} for item in result["exact_paths"])
    assert result["anatomy"]["missing"] == ["protected"]
