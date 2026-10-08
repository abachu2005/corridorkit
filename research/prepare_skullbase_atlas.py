#!/usr/bin/env python3
"""Bounded acquisition and inspection of UW's public skull-base CT atlas.

Research-only, noncommercial data; originals are never converted or overwritten.
Dependencies: requests, numpy; --contact-sheet also needs SimpleITK/matplotlib.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlparse

import numpy as np
import requests

RECORD_URL = (
    "https://digital.lib.washington.edu/researchworks/items/"
    "2b0f0e1e-ae81-4bf8-9b79-8dc6872b5ecc/full"
)
# Discovered in the public item's HTML, not a guessed byte-download endpoint.
ITEM_API = (
    "https://digital.lib.washington.edu/server/api/core/items/"
    "2b0f0e1e-ae81-4bf8-9b79-8dc6872b5ecc"
)
CT_NAME = "CT_template_pub - Neeraja Konuthula.nrrd"
SEG_NAME = "Segmentation_atlas_pub.seg.nrrd"
README_NAME = "instructions_readme.txt"
WANTED = (CT_NAME, SEG_NAME, README_NAME)
MAX_BYTES = 99_000_000  # strict decimal <100 MB, all selected originals combined
METADATA_LIMIT = 2_000_000
DISK_RESERVE = 512_000_000
HEADER_LIMIT = 262_144
CACHE = Path(__file__).resolve().parents[1] / "data-cache" / "skullbase-atlas"


def trusted_url(url: str) -> str:
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.netloc != "digital.lib.washington.edu"
            or not parsed.path.startswith("/server/api/")):
        raise ValueError(f"Unexpected repository API URL: {url}")
    return url


def api_json(session, url: str) -> dict:
    with session.get(trusted_url(url), stream=True, timeout=(15, 90),
                     allow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError(f"Repository metadata HTTP {response.status_code}: {url}")
        data = bytearray()
        for chunk in response.iter_content(65_536):
            if len(data) + len(chunk) > METADATA_LIMIT:
                raise ValueError("Metadata response exceeds bounded request size")
            data.extend(chunk)
    return json.loads(data)


def collection(session, url: str, key: str) -> list[dict]:
    result, seen = [], set()
    while url:
        if url in seen or len(seen) >= 10:
            raise ValueError("Unexpected repository pagination")
        seen.add(url)
        page = api_json(session, url)
        result.extend(page.get("_embedded", {}).get(key, []))
        url = page.get("_links", {}).get("next", {}).get("href")
        if not url and page.get("page", {}).get("number", 0) + 1 < page.get(
                "page", {}).get("totalPages", 1):
            raise ValueError("Incomplete repository pagination")
    return result


def select_originals(bitstreams: list[dict]) -> list[dict]:
    selected = []
    for name in WANTED:
        matches = [b for b in bitstreams if b["name"] == name
                   and b.get("bundleName") == "ORIGINAL"]
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one ORIGINAL named {name}")
        entry = matches[0]
        if not 0 < entry["sizeBytes"] < MAX_BYTES:
            raise ValueError("Original exceeds download limit")
        trusted_url(entry["_links"]["content"]["href"])
        if entry["checkSum"]["checkSumAlgorithm"].upper() != "MD5":
            raise ValueError("Unexpected repository checksum algorithm")
        selected.append(entry)
    if sum(b["sizeBytes"] for b in selected) > MAX_BYTES:
        raise ValueError("Combined original files exceed download limit")
    return selected


def discover(session) -> tuple[dict, list[dict]]:
    item = api_json(session, ITEM_API)
    if item.get("handle") != "1773/46259" or item.get("withdrawn"):
        raise ValueError("Unexpected or withdrawn atlas record")
    bundles = collection(session, item["_links"]["bundles"]["href"], "bundles")
    originals = [b for b in bundles if b["name"] == "ORIGINAL"]
    if len(originals) != 1:
        raise ValueError("Expected one ORIGINAL bundle")
    streams = collection(session, originals[0]["_links"]["bitstreams"]["href"], "bitstreams")
    return item, select_originals(streams)


def file_hashes(path: Path) -> dict:
    md5, sha256 = hashlib.md5(), hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1_048_576), b""):
            md5.update(chunk)
            sha256.update(chunk)
    return {"md5": md5.hexdigest(), "sha256": sha256.hexdigest()}


def download(session, entry: dict, cache: Path) -> dict:
    """Reuse verified bytes; refuse corrupt existing files; atomically publish new ones."""
    name, size = entry["name"], entry["sizeBytes"]
    if name not in WANTED or not 0 < size < MAX_BYTES:
        raise ValueError("File is outside the bounded original allowlist")
    target = cache / name
    if target.is_symlink():
        raise ValueError(f"Refusing symlink cache entry: {target}")
    reused = target.exists()
    if reused:
        if target.stat().st_size != size:
            raise ValueError(f"Existing cache has wrong size; inspect manually: {target}")
        hashes = file_hashes(target)
    else:
        if shutil.disk_usage(cache).free < size + DISK_RESERVE:
            raise ValueError("Insufficient disk headroom for bounded download")
        temp_path = None
        try:
            with session.get(trusted_url(entry["_links"]["content"]["href"]),
                             stream=True, timeout=(15, 90),
                             allow_redirects=False) as response:
                if response.status_code != 200:
                    raise ValueError(f"Download HTTP {response.status_code}: {name}")
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != size:
                    raise ValueError("Content-Length disagrees with repository metadata")
                with tempfile.NamedTemporaryFile(dir=cache, prefix=".atlas-", delete=False) as out:
                    temp_path = Path(out.name)
                    received = 0
                    for chunk in response.iter_content(1_048_576):
                        received += len(chunk)
                        if received > size:
                            raise ValueError("Stream exceeds advertised size")
                        out.write(chunk)
                if received != size:
                    raise ValueError("Truncated download")
            hashes = file_hashes(temp_path)
            if hashes["md5"] != entry["checkSum"]["value"].lower():
                raise ValueError("Repository MD5 mismatch")
            # Hard-link publish fails if another process created target; no clobber.
            os.link(temp_path, target)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    if hashes["md5"] != entry["checkSum"]["value"].lower():
        raise ValueError(f"Existing cache checksum mismatch: {target}")
    return {"name": name, "path": str(target), "size_bytes": size,
            **hashes, "reused_verified_cache": reused, "repository_metadata": entry}


def read_header(path: Path) -> dict[str, str]:
    fields = {}
    with path.open("rb") as stream:
        first = stream.readline(64)
        if not re.fullmatch(rb"NRRD000[1-5]\r?\n", first):
            raise ValueError("Not a supported NRRD header")
        used = len(first)
        while used <= HEADER_LIMIT:
            line = stream.readline(HEADER_LIMIT + 1)
            used += len(line)
            if used > HEADER_LIMIT or not line:
                break
            if line in (b"\n", b"\r\n"):
                return fields
            text = line.decode("utf-8").rstrip("\r\n")
            if text.startswith("#"):
                continue
            separator = ":=" if ":=" in text else ":"
            key, value = text.split(separator, 1)
            fields[key.strip()] = value.strip()
    raise ValueError("Missing or oversized NRRD header")


def vector(text: str) -> list[float]:
    return [float(x) for x in text.strip("()").split(",")]


def inspect_header(fields: dict[str, str]) -> dict:
    sizes = [int(x) for x in fields["sizes"].split()]
    directions = [None if x == "none" else vector(x) for x in
                  re.findall(r"none|\([^)]*\)", fields["space directions"])]
    if len(sizes) != int(fields["dimension"]) or len(directions) != len(sizes):
        raise ValueError("Inconsistent NRRD dimensions")
    axes = [i for i, d in enumerate(directions) if d is not None]
    if len(axes) != 3:
        raise ValueError("Expected three spatial axes")
    matrix = np.array([directions[i] for i in axes]).T
    origin = np.array(vector(fields["space origin"]))
    spatial_sizes = np.array([sizes[i] for i in axes])

    def bounds(lower, upper):
        corners = np.array(list(itertools.product(*zip(lower, upper))))
        physical = corners @ matrix.T + origin
        return {"min": physical.min(axis=0).tolist(), "max": physical.max(axis=0).tolist()}

    segments = []
    indices = sorted({int(m.group(1)) for key in fields
                      if (m := re.match(r"Segment(\d+)_", key))})
    for index in indices:
        prefix = f"Segment{index}_"
        segment = {k[len(prefix):]: v for k, v in fields.items() if k.startswith(prefix)}
        segment["index"] = index
        if "Extent" in segment:
            extent = np.array([int(x) for x in segment["Extent"].split()]).reshape(3, 2)
            segment["extent_center_bounds_native_mm"] = bounds(extent[:, 0], extent[:, 1])
        segments.append(segment)
    return {
        "dimension": len(sizes), "sizes_nrrd_axis_order": sizes,
        "space": fields.get("space"), "space_units": fields.get("space units", "not declared"),
        "directions": directions, "origin": origin.tolist(),
        "spatial_spacing_mm": np.linalg.norm(matrix, axis=0).tolist(),
        "voxel_center_bounds_native_mm": bounds(np.zeros(3), spatial_sizes - 1),
        "voxel_edge_bounds_native_mm": bounds(np.full(3, -0.5), spatial_sizes - 0.5),
        "segments": segments, "header_fields": fields,
    }


def write_json(path: Path, value: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".atlas-",
                                     delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2)
        stream.write("\n")
    temporary.replace(path)


def inspect_payload(cache: Path, inspection: dict, contact_sheet: bool) -> dict:
    """Read legacy independent binary components without flattening overlaps."""
    import SimpleITK as sitk

    segmentation = sitk.ReadImage(str(cache / SEG_NAME))
    data = sitk.GetArrayFromImage(segmentation)  # z, y, x, component
    segments = inspection[SEG_NAME]["segments"]
    if (data.ndim != 4 or data.shape[-1] != len(segments)
            or any("Layer" in s or "LabelValue" in s for s in segments)):
        raise ValueError("Payload inspection expects this atlas's legacy one-mask-per-component format")
    values = np.unique(data)
    if not set(values.tolist()).issubset({0, 1}):
        raise ValueError("Expected binary legacy component masks")
    mask_counts = []
    for component, segment in enumerate(segments):
        mask = data[..., component] != 0
        zz, yy, xx = np.where(mask)
        if not len(xx):
            raise ValueError(f"Empty segment: {segment['Name']}")
        actual_extent = [int(x) for pair in ((xx.min(), xx.max()), (yy.min(), yy.max()),
                                           (zz.min(), zz.max())) for x in pair]
        mask_counts.append({"name": segment["Name"], "component": component,
                            "nonzero_voxels": int(mask.sum()),
                            "actual_extent_ijk": actual_extent,
                            "header_extent_matches": actual_extent == [
                                int(x) for x in segment["Extent"].split()]})
    result = {
        "segmentation_array_order": "z,y,x,component",
        "segmentation_array_shape": list(data.shape), "stored_values": values.tolist(),
        "storage": f"legacy {len(segments)}-component binary masks; "
                   "no Layer/LabelValue header fields",
        "overlap_voxels": int(np.count_nonzero(np.count_nonzero(data, axis=-1) > 1)),
        "segment_counts": mask_counts,
    }
    if not contact_sheet:
        return result
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    ct = sitk.ReadImage(str(cache / CT_NAME))
    reference = sitk.VectorIndexSelectionCast(segmentation, 0)
    # Both readers convert their input coordinate convention to LPS.
    resampled = sitk.Resample(ct, reference, sitk.Transform(),
                              sitk.sitkLinear, -1024, sitk.sitkFloat32)
    image = sitk.GetArrayFromImage(resampled)
    # Crosshairs chosen from the atlas's pituitary extent, not a proposed route.
    pituitary = next(s for s in segments if s["Name"] == "pituitary")
    extent = np.array([int(v) for v in pituitary["Extent"].split()]).reshape(3, 2)
    x, y, z = np.rint(extent.mean(axis=1)).astype(int)
    sx, sy, sz = segmentation.GetSpacing()
    views = [
        ("Axial", (z, slice(None), slice(None)), sy / sx, "R → (native RAS)", "A →"),
        ("Coronal", (slice(None), y, slice(None)), sz / sx, "R → (native RAS)", "S →"),
        ("Sagittal", (slice(None), slice(None), x), sz / sy, "A → (native RAS)", "S →"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(15, 11), facecolor="white")
    for col, (name, index, aspect, xlabel, ylabel) in enumerate(views):
        plane = image[index]
        for row in (0, 1):
            ax = axes[row, col]
            ax.imshow(plane, cmap="gray", origin="lower", vmin=-500, vmax=1500,
                      aspect=aspect)
            ax.set_title(f"{name} — {'original masks over CT' if row else 'CT only'}")
            ax.set_xlabel(xlabel)
            ax.set_ylabel(ylabel)
            if row:
                for component, segment in enumerate(segments):
                    mask = data[..., component][index]
                    if mask.min() != mask.max():
                        color = [float(c) for c in segment["Color"].split()]
                        ax.contour(mask, levels=[0.5], colors=[color], linewidths=0.8)
    handles = [Line2D([0], [0], color=[float(c) for c in s["Color"].split()],
                      label=s["Name"]) for s in segments]
    fig.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.025),
               ncol=4, fontsize=8)
    fig.text(0.5, 0.008,
             "Source: Konuthula et al., UW ResearchWorks, hdl:1773/46259 — "
             "CC BY-NC-SA 3.0 US. Derived CT resampling and contour display.",
             ha="center", fontsize=8)
    fig.suptitle("UW averaged skull-base CT atlas — physical-coordinate inspection\n"
                 "Slices through atlas pituitary extent center; no route or safety claim",
                 fontsize=14)
    fig.tight_layout(rect=(0, 0.13, 1, 0.94))
    output = cache / "atlas-contact-sheet.png"
    fig.savefig(output, dpi=130)
    plt.close(fig)
    result["contact_sheet"] = {
        "path": str(output), "sha256": file_hashes(output)["sha256"],
        "slice_center_segmentation_ijk": [int(x), int(y), int(z)],
        "slice_center_lps_mm": list(reference.TransformIndexToPhysicalPoint(
            (int(x), int(y), int(z)))),
        "display_window": [-500, 1500],
        "method": "CT linearly resampled in memory to segmentation physical grid; "
                  "each original binary component contoured independently",
        "ct_range_on_segmentation_grid": [float(image.min()), float(image.max())],
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--inspect-payload", action="store_true",
                        help="Verify independent component masks (requires SimpleITK)")
    parser.add_argument("--contact-sheet", action="store_true",
                        help="Inspect payload and render CT/mask slices (also needs matplotlib)")
    args = parser.parse_args()
    cache = args.cache.expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    with requests.Session() as session:
        item, selected = discover(session)
        files = [download(session, entry, cache) for entry in selected]
    inspection = {name: inspect_header(read_header(cache / name)) for name in (CT_NAME, SEG_NAME)}
    manifest = {
        "schema_version": 1, "inspected_at_utc": datetime.now(timezone.utc).isoformat(),
        "record_url": RECORD_URL, "item_api_url": ITEM_API, "source_item": item,
        "license": {k: item["metadata"].get(k) for k in ("dc.rights", "dc.rights.uri")},
        "download_cap_bytes": MAX_BYTES, "original_bytes": sum(f["size_bytes"] for f in files),
        "files": files, "inspection": inspection,
        "original_readme": (cache / README_NAME).read_text(),
        "limitations": [
            "Averaged six-subject CT template, not an individual patient CT.",
            "Anterior skull-base atlas; no proven EEA/CTM route or safety conclusion.",
            "Legacy independent binary components preserve overlaps; originals unchanged.",
            "Data license is CC BY-NC-SA 3.0 US, separate from application code license.",
        ],
    }
    if args.inspect_payload or args.contact_sheet:
        manifest["payload_inspection"] = inspect_payload(cache, inspection, args.contact_sheet)
    write_json(cache / "manifest.json", manifest)
    print(json.dumps({"manifest": str(cache / "manifest.json"),
                      "original_bytes": manifest["original_bytes"],
                      "segments": [s["Name"] for s in inspection[SEG_NAME]["segments"]],
                      "readme": manifest["original_readme"]}, indent=2))


if __name__ == "__main__":
    main()
