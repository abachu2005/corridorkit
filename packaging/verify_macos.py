"""Verify the frozen executable from outside the checkout with a clean runtime env."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--app", type=Path, default=root / "dist/SkullBaseCorridor.app")
    parser.add_argument("--output", type=Path, default=root / "research/native-bundle-evidence/frozen")
    args = parser.parse_args()
    bundle = args.app.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    executable = bundle / "Contents/MacOS/SkullBaseCorridor"
    if not executable.is_file():
        raise SystemExit(f"Missing frozen executable: {executable}")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "CONDA", "QT_", "DYLD_", "VIRTUAL_ENV"))
    }
    environment["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    report_path = output / "report.json"
    if report_path.exists():
        raise SystemExit("Evidence directory already contains a report; use a fresh --output.")
    with tempfile.TemporaryDirectory(prefix="corridor-frozen-cwd-") as cwd:
        process = subprocess.run(
            [str(executable), "--self-test", str(output)],
            cwd=cwd, env=environment, capture_output=True, text=True, timeout=180,
        )
    (output / "stdout.txt").write_text(process.stdout)
    (output / "stderr.txt").write_text(process.stderr)
    signature = subprocess.run(
        ["codesign", "--verify", "--deep", "--strict", str(bundle)],
        capture_output=True, text=True,
    )
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    hashes = {}
    for folder in ("src", "packaging"):
        for path in sorted((root / folder).rglob("*.py")):
            hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path = bundle / "Contents/Resources/build-manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    source_matches = bool(manifest) and manifest.get("source_sha256") == hashes
    verification = {
        "passed": process.returncode == 0 and report.get("passed") is True
                  and report.get("frozen") is True and signature.returncode == 0
                  and source_matches,
        "exit_code": process.returncode,
        "signature_verify_exit_code": signature.returncode,
        "signature_verify_stderr": signature.stderr,
        "platform": platform.platform(),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "current_source_sha256": hashes,
        "embedded_build_manifest": manifest,
        "build_source_matches_checkout": source_matches,
        "bundle": str(bundle),
        "runtime_environment": "Outside checkout; Python/Conda/Qt/DYLD variables removed; system PATH.",
        "limitation": "Local ad-hoc signature verification is not Developer ID signing/notarization.",
    }
    (output / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    print(json.dumps({k: v for k, v in verification.items() if k != "current_source_sha256"}, indent=2))
    if not verification["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
