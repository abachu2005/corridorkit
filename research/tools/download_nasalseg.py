"""Download the public v2 archive without buffering or unpacking it.

Run: python research/tools/download_nasalseg.py
Only this program's .partial file is removed on failure; existing archives are
never replaced unless their checksum matches the published source checksum.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

URL = "https://zenodo.org/api/records/13893419/files/NasalSeg.zip/content"
EXPECTED_MD5 = "ba83df47974d907798542101fa43ac7d"
ROOT = Path(__file__).resolve().parents[2]
DESTINATION = ROOT / "research/data-cache/NasalSeg-v2.zip"


def checksums(path: Path) -> dict:
    md5, sha = hashlib.md5(), hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            md5.update(chunk)
            sha.update(chunk)
            size += len(chunk)
    return {"md5": md5.hexdigest(), "sha256": sha.hexdigest(), "bytes": size}


def main() -> None:
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    if not DESTINATION.exists():
        if shutil.disk_usage(DESTINATION.parent).free < 400 * 1024**2:
            raise RuntimeError("Need 400 MiB free for archive and one-case working space")
        partial = DESTINATION.with_suffix(".zip.partial")
        # Exclusive creation prevents deletion or overwrite of another run's file.
        with partial.open("xb") as output:
            try:
                request = urllib.request.Request(URL, headers={"User-Agent": "corridorkit-research/1"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    total = 0
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > 260 * 1024**2:
                            raise RuntimeError("Archive exceeds prespecified 260 MiB download cap")
                        output.write(chunk)
                output.flush()
            except BaseException:
                partial.unlink(missing_ok=True)
                raise
        hashes = checksums(partial)
        if hashes["md5"] != EXPECTED_MD5:
            partial.unlink()
            raise RuntimeError(f"Published MD5 mismatch: {hashes}")
        partial.rename(DESTINATION)
    hashes = checksums(DESTINATION)
    if hashes["md5"] != EXPECTED_MD5:
        raise RuntimeError("Existing archive has incorrect checksum; left untouched")
    provenance = {
        "url": URL, "zenodo_record": "13893419", "version": "v2",
        "license": "CC-BY-4.0", "expected_md5": EXPECTED_MD5,
        "path": str(DESTINATION.relative_to(ROOT)), **hashes,
    }
    (DESTINATION.parent / "NasalSeg-v2-source.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()
