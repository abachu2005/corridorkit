"""Application orchestration kept separate from CLI and geometry."""

from pathlib import Path

from corridorkit.export.json import export_result, read_case, input_manifest
from corridorkit.geometry.engine import analyze_case


def analyze_file(
    input_path: str | Path,
    output_path: str | Path,
    *,
    simultaneous_minimum_angle_deg: float | None = None,
):
    case = read_case(input_path)
    base = Path(input_path).resolve().parent
    before = input_manifest(case, base)
    result = analyze_case(
        case,
        simultaneous_minimum_angle_deg=simultaneous_minimum_angle_deg,
        base_directory=Path(input_path).resolve().parent,
    )
    if input_manifest(case, base) != before:
        raise ValueError("Input files changed during analysis")
    export_result(
        output_path,
        case,
        result,
        analysis_options={
            "simultaneous_minimum_angle_deg": simultaneous_minimum_angle_deg
        },
        base_directory=base,
    )
    return result
