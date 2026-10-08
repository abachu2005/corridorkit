"""Extract one checksum-verified public example for the desktop smoke test."""
from pathlib import Path
import json
import zipfile
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    archive_path = ROOT / "research/data-cache/NasalSeg-v2.zip"
    digest = hashlib.md5()
    with archive_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != "ba83df47974d907798542101fa43ac7d":
        raise ValueError("Unexpected archive")
    manifest = json.loads((ROOT / "research/results/airspace-v1/frozen-manifest.json").read_text())
    # Choose a development-only image for UI inspection.
    identifier = next(key for key, value in manifest["subject_split"].items()
                      if value == "development")
    destination = ROOT / "research/data-cache/desktop-smoke"
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for role, name in [("ct", manifest["image_members"][identifier]),
                           ("labels", manifest["label_members"][identifier])]:
            (destination / f"{role}.nrrd").write_bytes(archive.read(name))
    import nibabel as nib
    from skullbase_corridor.io.volumes import load_volume
    from skullbase_corridor.export.json import atomic_json_write, file_sha256
    conversions = []
    for role in ("ct", "labels"):
        source = destination / f"{role}.nrrd"
        # This is a documented dataset-specific assumption, never a loader default.
        volume = load_volume(source, assume_spatial_unit="mm")
        output = destination / f"{role}.nii.gz"
        image = nib.Nifti1Image(volume.data, volume.affine)
        image.header.set_xyzt_units("mm")
        nib.save(image, output)
        conversions.append({"source_sha256": file_sha256(source),
                            "output_sha256": file_sha256(output),
                            "assumed_spatial_units": "mm", "role": role})
    atomic_json_write(destination / "conversion.json", {"case_id": identifier, "conversions": conversions})
    import gzip
    import numpy as np
    from scipy import ndimage
    from skullbase_corridor.domain.models import CorridorCase
    from skullbase_corridor.export.json import write_case
    records = ROOT / "research/results/production-public-v2/per-case.jsonl.gz"
    if records.exists():
        with gzip.open(records, "rt") as stream:
            record = next(json.loads(line) for line in stream
                          if json.loads(line)["case_id"] == identifier)
        data = record["configurations"]["finite"]
        label = load_volume(destination / "labels.nii.gz")
        boundary = ndimage.binary_dilation(label.data > 0) & ~(label.data > 0)
        np.save(destination / "boundary.npy", boundary)
        data["source_image"] = {"uri": str(destination / "ct.nii.gz"),
                                "sha256": file_sha256(destination / "ct.nii.gz")}
        data["protected_structures"][0]["geometry"]["uri"] = str(destination / "boundary.npy")
        data["case_id"] = f"{identifier}-COMPUTATIONAL-NOT-SURGICAL"
        write_case(destination / "computational-case.json", CorridorCase.model_validate(data))
    print(identifier, destination / "ct.nii.gz")


if __name__ == "__main__":
    main()
