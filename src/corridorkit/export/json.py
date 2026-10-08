"""Canonical, checksummed JSON interchange."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import csv
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from corridorkit.domain.models import CorridorCase


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def checksum(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def software_version() -> str:
    try:
        return version("corridorkit")
    except PackageNotFoundError:
        return "0.3.0"


def atomic_json_write(path: str | Path, value: Any) -> None:
    destination = Path(path)
    text = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".corridor-", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_case(path: str | Path, case: CorridorCase) -> None:
    atomic_json_write(path, case.model_dump(mode="json"))


def read_case(path: str | Path) -> CorridorCase:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return CorridorCase.model_validate(value.get("case", value))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def input_manifest(case: CorridorCase, base_directory: Path | None = None) -> list[dict]:
    """Fingerprint external geometry without exposing local paths in exports."""
    references: list[tuple[str, str, str | None]] = []
    if case.source_image:
        references.append(("source_image", case.source_image.uri, case.source_image.sha256))
    for index, structure in enumerate(case.protected_structures):
        geometry = structure.geometry
        if geometry and hasattr(geometry, "uri"):
            references.append((f"protected_{index}", geometry.uri, None))
    for index, approach in enumerate(case.approaches):
        for rindex, removal in enumerate(approach.virtual_bone_removals):
            references.append((f"removal_{index}_{rindex}", removal.geometry.uri, None))
    records = []
    for role, uri, expected in references:
        path = Path(uri)
        if not path.is_absolute():
            path = (base_directory or Path.cwd()) / path
        if not path.is_file():
            records.append({"role": role, "status": "unavailable", "sha256": None})
            continue
        actual = file_sha256(path)
        if expected is not None and actual != expected:
            raise ValueError(f"{role} checksum differs from the approved source")
        records.append({"role": role, "status": "available", "sha256": actual})
    return records


def export_result(
    path: str | Path,
    case: CorridorCase,
    result: BaseModel,
    *,
    analysis_options: dict[str, Any] | None = None,
    base_directory: Path | None = None,
) -> dict[str, Any]:
    if getattr(result, "case_id", None) != case.case_id:
        raise ValueError("result belongs to a different case")
    source = case.model_dump(mode="json")
    result_data = result.model_dump(mode="json")
    # Include replay geometry but never disclose local image/mask filesystem paths.
    geometry_record = json.loads(json.dumps(source))
    if geometry_record["source_image"]:
        geometry_record["source_image"]["uri"] = "input:source_image"
    for index, structure in enumerate(geometry_record["protected_structures"]):
        geometry = structure["geometry"]
        if geometry and "uri" in geometry:
            geometry["uri"] = f"input:protected_{index}"
    for index, approach in enumerate(geometry_record["approaches"]):
        for rindex, removal in enumerate(approach["virtual_bone_removals"]):
            removal["geometry"]["uri"] = f"input:removal_{index}_{rindex}"
    envelope = {
        "schema_version": case.schema_version,
        "software_version": software_version(),
        "case_id": case.case_id,
        "source_sha256": checksum(source),
        "input_files": input_manifest(case, base_directory),
        "analysis_options": analysis_options or {},
        "configuration": geometry_record["approaches"],
        "geometry": geometry_record,
        "result": result_data,
        "result_sha256": checksum(result_data),
    }
    atomic_json_write(path, envelope)
    return envelope


def export_csv(path: str | Path, result: BaseModel) -> None:
    fields = ["approach", "status", "target_count", "reached_count",
              "reached_volume_mm3", "solid_angle_sr", "working_depth_mm", "clearance_mm"]
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in result.approaches:
            unavailable = item.status.value in ("abstained", "incomplete", "unsupported")
            name = item.name
            if name.startswith(("=", "+", "-", "@")):
                name = "'" + name
            writer.writerow(dict(
                approach=name, status=item.status.value, target_count=item.target_count,
                reached_count=None if unavailable else len(item.reached_point_indices),
                reached_volume_mm3=None if unavailable else item.reached_measure_mm3,
                solid_angle_sr=None if unavailable else item.feasible_solid_angle_sr,
                working_depth_mm=None if unavailable else item.best_working_depth_mm,
                clearance_mm=None if unavailable else item.minimum_clearance_mm,
            ))
