"""Versioned local review state, independent from Qt and geometry."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from corridorkit.domain.models import CorridorCase
from corridorkit.export.json import atomic_json_write, checksum, input_manifest


class ReviewEvent(BaseModel):
    timestamp: str
    action: str
    reviewer: str
    case_sha256: str
    detail: str = ""


class IntendedPath(BaseModel):
    """A user-authored line, never an analyzed feasible witness."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)
    approach_name: str
    entry_mm: tuple[float, float, float]
    tip_mm: tuple[float, float, float]
    coordinate_frame: str = "RAS"

    @model_validator(mode="after")
    def valid_line(self):
        import numpy as np
        if self.coordinate_frame != "RAS":
            raise ValueError("Intended paths require RAS mm")
        if np.linalg.norm(np.array(self.tip_mm) - self.entry_mm) < 1e-6:
            raise ValueError("Entry and tip must be distinct")
        return self


class ReviewDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_version: str = "1"
    case: CorridorCase
    events: list[ReviewEvent] = Field(default_factory=list)
    anatomy_approval_sha256: str | None = None
    approved_inputs: list[dict] | None = None
    registration: dict[str, Any] | None = None
    intended_paths: list[IntendedPath] = Field(default_factory=list)
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    def fingerprint(self) -> str:
        return checksum(self.case.model_dump(mode="json"))

    @property
    def anatomy_approved(self) -> bool:
        if self.anatomy_approval_sha256 != self.fingerprint() or self.approved_inputs is None:
            return False
        try:
            return self.approved_inputs == input_manifest(self.case)
        except (ValueError, OSError):
            return False

    def record(self, action: str, reviewer: str, detail: str = "") -> None:
        self.events.append(ReviewEvent(
            timestamp=datetime.now(UTC).isoformat(),
            action=action, reviewer=reviewer,
            case_sha256=self.fingerprint(), detail=detail,
        ))

    def approve_anatomy(self, reviewer: str) -> None:
        if not reviewer.strip():
            raise ValueError("reviewer identity is required")
        inputs = input_manifest(self.case)
        if any(item["status"] != "available" for item in inputs):
            raise ValueError("all referenced inputs must be available for review")
        self.approved_inputs = inputs
        self.anatomy_approval_sha256 = self.fingerprint()
        self.record("anatomy_reviewed", reviewer)

    def replace_case(self, case: CorridorCase, detail: str = "edit") -> None:
        self.case = case
        self.anatomy_approval_sha256 = None
        self.approved_inputs = None
        self.record("case_changed", "", detail)

    def save(self, path: Path) -> None:
        atomic_json_write(path, self.model_dump(mode="json"))
