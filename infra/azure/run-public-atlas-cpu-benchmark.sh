#!/usr/bin/env bash
set -euo pipefail

# Runs inside a disposable/public-research Azure worker. It deliberately uses
# only the public UW averaged atlas and emits observed timings; it does not
# claim patient-level quality or GPU performance.
work=${CORRIDORKIT_BENCHMARK_DIR:-"${HOME:-/tmp}/corridorkit-benchmark"}
mkdir -p "$work"
cd "$work"

atlas_url="https://digital.lib.washington.edu/server/api/core/bitstreams/59e583ef-faaa-47c7-975d-d63f187e57ad/content"
atlas_sha256="40b16cf04998c5b2fb865d4baf4d86981ae4f5d252b058b46abc3ce2684e44d5"

stage_start=$(date +%s%N)
if [[ ! -f atlas.nrrd ]] || [[ "$(sha256sum atlas.nrrd | cut -d' ' -f1)" != "$atlas_sha256" ]]; then
  curl --fail --location --retry 3 --output atlas.nrrd "$atlas_url"
fi
echo "$atlas_sha256  atlas.nrrd" | sha256sum --check
download_ms=$(( ($(date +%s%N) - stage_start) / 1000000 ))

stage_start=$(date +%s%N)
if [[ ! -x venv/bin/python ]]; then
  python3 -m venv venv
fi
venv/bin/python -m pip install --quiet --upgrade pip
venv/bin/python -m pip install --quiet "TotalSegmentator==2.11.0" "SimpleITK>=2.3" psutil
setup_ms=$(( ($(date +%s%N) - stage_start) / 1000000 ))

stage_start=$(date +%s%N)
venv/bin/python - <<'PY'
import SimpleITK as sitk
image = sitk.ReadImage("atlas.nrrd")
sitk.WriteImage(image, "atlas.nii.gz")
PY
preprocess_ms=$(( ($(date +%s%N) - stage_start) / 1000000 ))

rm -rf masks
stage_start=$(date +%s%N)
timeout "${CORRIDORKIT_MODEL_TIMEOUT:-80m}" \
  venv/bin/TotalSegmentator \
  -i atlas.nii.gz \
  -o masks \
  -ta head_glands_cavities \
  -d cpu
inference_ms=$(( ($(date +%s%N) - stage_start) / 1000000 ))

venv/bin/python - "$download_ms" "$setup_ms" "$preprocess_ms" "$inference_ms" <<'PY'
import hashlib
import json
import platform
import resource
import sys
from pathlib import Path

download, setup, preprocess, inference = map(int, sys.argv[1:])
outputs = sorted(Path("masks").glob("*.nii.gz"))
print(json.dumps({
    "schema_version": "entry-pipeline-azure-observation-v1",
    "configuration_id": "totalsegmentator-2.11.0-head-glands-cavities-full-cpu",
    "run_state": "cold",
    "success": bool(outputs),
    "timings_ms": {
        "upload": download,
        "model_load": setup,
        "preprocess": preprocess,
        "inference": inference,
        "total": download + setup + preprocess + inference,
    },
    "memory_peak_mb": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024,
    "scan": {
        "name": "UW averaged skull-base CT atlas",
        "sha256": hashlib.sha256(Path("atlas.nrrd").read_bytes()).hexdigest(),
        "size_bytes": Path("atlas.nrrd").stat().st_size,
    },
    "hardware": {"platform": platform.platform(), "accelerator": "none", "device": "cpu"},
    "outputs": [path.name for path in outputs],
    "quality": None,
    "provenance": {
        "source": "observed Azure VM process timings",
        "timing_basis": "monotonic wall-clock observations",
        "public_data_only": True,
        "limitations": [
            "CPU fallback because subscription GPU-family quota was zero",
            "averaged reference atlas, not an independent patient or accuracy benchmark",
            "no quality metrics because matching task ground truth is unavailable",
        ],
    },
}, sort_keys=True))
PY
