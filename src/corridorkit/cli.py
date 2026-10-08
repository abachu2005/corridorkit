"""Command-line interface."""

from __future__ import annotations

import platform
import json
from pathlib import Path

import numpy
import pydantic
import typer

from corridorkit.application.service import analyze_file
from corridorkit.analysis.convergence import convergence_study
from corridorkit.analysis.robustness import perturbation_study
from corridorkit.data.audit import audit_dataset, write_audit
from corridorkit.export.json import read_case, write_case
from corridorkit.synthetic.cases import analytical_case

app = typer.Typer(no_args_is_help=True, help="CorridorKit: physical-space skull-base corridor analysis.")


@app.command()
def synthetic(output: Path) -> None:
    """Write a deterministic synthetic case."""
    write_case(output, analytical_case())
    typer.echo(f"Wrote {output}")


@app.command()
def analyze(
    input: Path,
    output: Path,
    simultaneous_minimum_angle_deg: float | None = typer.Option(
        None, min=0.0, max=180.0, help="Also evaluate simultaneous approach pairs."
    ),
) -> None:
    """Analyze a case and write a checksummed result envelope."""
    result = analyze_file(
        input,
        output,
        simultaneous_minimum_angle_deg=simultaneous_minimum_angle_deg,
    )
    summaries = []
    for item in result.approaches:
        summary = (
            f"{item.name}={item.status.value}, "
            f"reached={'unavailable' if item.status.value in ('abstained', 'incomplete') else str(len(item.reached_point_indices)) + '/' + str(item.target_count)}, "
            f"solid_angle_sr={item.feasible_solid_angle_sr}"
        )
        if item.witness_polar_angle_deg is not None:
            summary += (
                f", witness_polar_deg={item.witness_polar_angle_deg:.3f}, "
                f"witness_azimuth_deg={item.witness_azimuth_deg:.3f}"
            )
        summaries.append(summary)
    typer.echo(f"Wrote {output}: " + "; ".join(summaries))
    for pair in result.simultaneous_pairs:
        typer.echo(
            f"pair {pair.first}+{pair.second}: feasible={str(pair.feasible).lower()}, "
            f"reached={len(pair.first_reached_point_indices)}+"
            f"{len(pair.second_reached_point_indices)}"
        )


@app.command()
def doctor() -> None:
    """Report runtime and dependency health."""
    checks = {
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "pydantic": pydantic.__version__,
        "physical_units": "millimetres",
    }
    for name, value in checks.items():
        typer.echo(f"{name}: {value}")
    if tuple(map(int, platform.python_version_tuple())) < (3, 11, 0):
        raise typer.Exit(code=1)
    typer.echo("status: ok")


@app.command("audit-dataset")
def audit_dataset_command(
    images: Path,
    labels: Path,
    output: Path,
    source_version: str = typer.Option("unknown"),
    sample_limit: int | None = typer.Option(None, min=1),
) -> None:
    """Audit public image/label geometry without asserting surgical validity."""
    audit = audit_dataset(
        images,
        labels,
        source_version=source_version,
        sample_limit=sample_limit,
    )
    write_audit(output, audit)
    typer.echo(
        f"Wrote {output}: paired={audit['paired_count']}, "
        f"engineering_eligible={audit['engineering_eligible_count']}"
    )


@app.command("convergence")
def convergence_command(input: Path, output: Path) -> None:
    """Run the frozen trajectory-sampling convergence grid."""
    result = convergence_study(read_case(input), [(5, 24), (9, 48), (17, 96)],
                               base_directory=input.resolve().parent)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    typer.echo(f"Wrote {output}: rows={len(result['rows'])}")


@app.command("sensitivity")
def sensitivity_command(
    input: Path,
    output: Path,
    repeats: int = typer.Option(200, min=2),
    portal_sigma_mm: float = typer.Option(1.0, min=0),
    seed: int = typer.Option(20261001),
) -> None:
    """Run documented portal-placement sensitivity analysis."""
    result = perturbation_study(
        read_case(input),
        repeats=repeats,
        portal_sigma_mm=portal_sigma_mm,
        seed=seed,
        base_directory=input.resolve().parent,
    )
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    typer.echo(f"Wrote {output}: repeats={repeats}")


@app.command("suggest-ct")
def suggest_ct(input: Path, output_directory: Path, bone_hu: float = 300,
               air_hu: float = -500) -> None:
    """Write explicitly unreviewed bone/air threshold proposals."""
    import nibabel as nib
    from corridorkit.io.volumes import load_volume
    from corridorkit.anatomy.suggestions import suggest_ct_masks
    from corridorkit.export.json import atomic_json_write, file_sha256
    volume = load_volume(input)
    result = suggest_ct_masks(volume.data, volume.affine, bone_hu=bone_hu, air_hu=air_hu)
    output_directory.mkdir(parents=True, exist_ok=True)
    for name, mask in result.pop("masks").items():
        destination = output_directory / f"{name}-unreviewed.nii.gz"
        if destination.exists():
            raise typer.BadParameter(f"Refusing to overwrite {destination}")
        image = nib.Nifti1Image(mask.astype("uint8"), volume.affine)
        image.header.set_xyzt_units("mm")
        nib.save(image, destination)
    result["input_sha256"] = file_sha256(input)
    atomic_json_write(output_directory / "suggestions.json", result)
    typer.echo("Suggestions written; review required. No critical anatomy was inferred.")


@app.command("register-mri")
def register_mri(fixed_ct: Path, moving_mri: Path, output_transform: Path) -> None:
    """Generate a CT-to-MRI resampling transform proposal in physical LPS."""
    from corridorkit.io.registration import register_rigid
    result = register_rigid(fixed_ct, moving_mri, output_transform)
    typer.echo(json.dumps(result, indent=2))


@app.command("convert-volume")
def convert_volume(input: Path, output: Path,
                   assume_spatial_unit: str | None = None,
                   series_uid: str | None = None) -> None:
    """Convert a declared/explicitly assumed source frame into RAS-mm NIfTI."""
    import nibabel as nib
    from corridorkit.io.volumes import load_volume
    from corridorkit.export.json import atomic_json_write, file_sha256
    if output.exists():
        raise typer.BadParameter("Output exists; choose a new path")
    volume = load_volume(input, series_uid=series_uid, assume_spatial_unit=assume_spatial_unit)
    image = nib.Nifti1Image(volume.data, volume.affine)
    image.header.set_xyzt_units("mm")
    nib.save(image, output)
    atomic_json_write(output.with_name(output.name + ".provenance.json"), {
        "source_sha256": file_sha256(input) if input.is_file() else None,
        "output_sha256": file_sha256(output), "series_uid": series_uid,
        "explicit_assumption": assume_spatial_unit, "metadata": volume.metadata,
        "coordinate_frame": "RAS_mm", "intensity_unit": volume.intensity_unit,
    })
    typer.echo(f"Wrote {output}; spatial-unit assumptions recorded in provenance sidecar")


@app.command("review-registration")
def review_registration_command(proposal: Path, landmarks: Path, reviewer: str,
                                maximum_error_mm: float,
                                overlays_reviewed: bool = False) -> None:
    """Review using matched fixed_lps/moving_lps arrays and an explicit tolerance."""
    from corridorkit.io.registration import review_registration
    points = json.loads(landmarks.read_text())
    result = review_registration(
        proposal, reviewer=reviewer, fixed_lps=points["fixed_lps"],
        moving_lps=points["moving_lps"], maximum_error_mm=maximum_error_mm,
        overlays_reviewed=overlays_reviewed,
    )
    typer.echo(result["review_status"])


@app.command("resample-mri")
def resample_mri(proposal: Path, output: Path) -> None:
    """Resample MRI only from an unchanged, explicitly reviewed proposal."""
    from corridorkit.io.registration import resample_reviewed_mri
    resample_reviewed_mri(proposal, output)
    typer.echo(f"Wrote {output}")


if __name__ == "__main__":
    app()
