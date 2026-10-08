"""Release-bound synthetic checks and public provenance for CorridorKit.

Run from a clean detached worktree at a tag. Outputs are written outside it.
This runner never edits the tested source or uses patient data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], *, env=None, timeout=900) -> subprocess.CompletedProcess:
    print("Running:", " ".join(command), flush=True)
    return subprocess.run(command, cwd=ROOT, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slicer", type=Path, required=True)
    parser.add_argument("--tag", default="v0.3.0")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    commit = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    tag_commit = run(["git", "rev-parse", args.tag + "^{commit}"]).stdout.strip()
    assert commit == tag_commit, "Runner must execute from the exact tag"
    assert not run(["git", "status", "--porcelain", "--untracked-files=no"]).stdout.strip()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["CORRIDORKIT_PYTHON"] = sys.executable
    environment["QT_QPA_PLATFORM"] = "offscreen"
    source = run([
        sys.executable, "packaging/verify_source_release.py", "--desktop",
        "--tests", "--build", "--output", str(output / "artifacts"),
        "--report", str(output / "source-verification.json"), "--timeout", "600",
    ], env=environment, timeout=2400)
    (output / "source-verification.log").write_text(source.stdout)
    assert source.returncode == 0, source.stdout[-5000:]
    wheel = run([
        sys.executable, "packaging/verify_wheel.py",
        str(output / "artifacts/corridorkit-0.3.0-py3-none-any.whl"),
    ], env=environment)
    (output / "wheel-verification.log").write_text(wheel.stdout)
    assert wheel.returncode == 0, wheel.stdout
    slicer_package = run([
        sys.executable, "packaging/build_slicer_package.py",
        "--wheelhouse", str(output / "artifacts"),
        "--output", str(output / "artifacts/CorridorKit-Slicer-5.12-0.3.0.zip"),
    ], env=environment)
    (output / "slicer-package-build.log").write_text(slicer_package.stdout)
    assert slicer_package.returncode == 0, slicer_package.stdout
    package_check = run([
        sys.executable, "packaging/verify_slicer_package.py",
        str(output / "artifacts/CorridorKit-Slicer-5.12-0.3.0.zip"),
    ], env=environment)
    (output / "slicer-package-verification.log").write_text(package_check.stdout)
    assert package_check.returncode == 0, package_check.stdout
    phantom = output / "phantom"
    generated = run([
        sys.executable, "-c",
        "from corridorkit.synthetic.planning import write_planning_phantom; "
        f"print(write_planning_phantom({str(phantom)!r}))",
    ], env=environment)
    assert generated.returncode == 0, generated.stdout
    quantitative = run([
        sys.executable, "-c",
        "import json; from pathlib import Path; "
        "from corridorkit.export.json import read_case; "
        "from corridorkit.geometry.engine import analyze_case; "
        "from corridorkit.analysis.coverage import build_coverage_comparison; "
        f"p=Path({str(phantom / 'SYNTHETIC-planning.case.json')!r}); "
        "case=read_case(p); result=analyze_case(case, base_directory=p.parent); "
        "comparison=build_coverage_comparison(case,result); "
        "summary={'target_samples': comparison.target_count, "
        "'coverage_counts': comparison.counts, 'approaches': "
        "[{'name': a.name, 'feasible_trajectory_count': a.feasible_trajectory_count, "
        "'reached_samples': len(a.reached_point_indices)} for a in result.approaches]}; "
        "assert summary['target_samples']==413; "
        "assert summary['coverage_counts']=={'eea_only':59,'tm_only':232,'both':35,"
        "'not_reached':87,'unavailable':0}; "
        "assert [a['feasible_trajectory_count'] for a in summary['approaches']]==[27,77]; "
        "print(json.dumps(summary))",
    ], env=environment)
    assert quantitative.returncode == 0, quantitative.stdout
    (output / "quantitative-summary.json").write_text(quantitative.stdout)
    desktop_environment = dict(environment)
    desktop_environment.pop("QT_QPA_PLATFORM", None)
    desktop = run([
        sys.executable, "research/planning_desktop_smoke.py",
        "--case", str(phantom / "SYNTHETIC-planning.case.json"),
        "--output", str(output / "worked-example"),
    ], env=desktop_environment)
    (output / "desktop-verification.log").write_text(desktop.stdout)
    assert desktop.returncode == 0, desktop.stdout
    slicer_environment = dict(desktop_environment)
    # Slicer's Python must not inherit the external engine's module search path.
    slicer_environment.pop("PYTHONPATH", None)
    slicer_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    native = run([
        str(args.slicer), "--disable-settings", "--disable-cli-modules",
        "--no-splash", "--python-script", str(ROOT / "slicer/verify_in_slicer.py"),
    ], env=slicer_environment, timeout=600)
    (output / "slicer-verification.log").write_text(native.stdout)
    assert native.returncode == 0, native.stdout[-5000:]
    result_path = ROOT / "research/slicer-verification/integration.json"
    native_result = json.loads(result_path.read_text())
    assert native_result["slicer_integration_passed"]
    (output / "slicer-integration.json").write_text(json.dumps(native_result, indent=2) + "\n")
    source_report = json.loads((output / "source-verification.json").read_text())
    report = {
        "software": "CorridorKit", "version": "0.3.0",
        "git_tag": args.tag, "git_commit": commit,
        "operating_system": platform.platform(), "python": sys.version,
        "source_and_tests_passed": source_report["passed"],
        "wheel_passed": wheel.returncode == 0,
        "slicer_package_passed": package_check.returncode == 0,
        "native_desktop": json.loads((output / "worked-example/report.json").read_text()),
        "worked_example": json.loads(quantitative.stdout),
        "native_slicer": native_result,
        "protocol_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "slicer_script_sha256": hashlib.sha256(
            (ROOT / "slicer/verify_in_slicer.py").read_bytes()).hexdigest(),
        "source_unchanged": not run(
            ["git", "status", "--porcelain", "--untracked-files=no"]).stdout.strip(),
    }
    (output / "release-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    assert report["source_unchanged"]
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
