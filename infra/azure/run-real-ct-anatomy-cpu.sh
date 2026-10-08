#!/usr/bin/env bash
set -euo pipefail

# Public/deidentified NasalSeg case only. Azure VM managed identity must have
# blob contributor access; no storage keys or SAS tokens are accepted.
work=${SKULLBASE_REAL_CT_DIR:-/mnt/skullbase-real-ct}
account=${SKULLBASE_STORAGE_ACCOUNT:-autohijdra51cac1f2}
input_blob=${SKULLBASE_INPUT_BLOB:-skullbase-corridor/nasalseg/P001_img.nrrd}
output_prefix=${SKULLBASE_OUTPUT_PREFIX:-skullbase-corridor/real-ct-p001-20261007}
mkdir -p "$work"
cd "$work"

token=$(curl --fail --silent --show-error \
  -H Metadata:true \
  'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https%3A%2F%2Fstorage.azure.com%2F' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')
storage_get() {
  curl --fail --silent --show-error --location \
    -H "Authorization: Bearer $token" \
    -H "x-ms-version: 2023-11-03" \
    "https://${account}.blob.core.windows.net/$1" --output "$2"
}
storage_put() {
  curl --fail --silent --show-error --request PUT \
    -H "Authorization: Bearer $token" \
    -H "x-ms-version: 2023-11-03" \
    -H "x-ms-date: $(LC_ALL=C date -u '+%a, %d %b %Y %H:%M:%S GMT')" \
    -H "x-ms-blob-type: BlockBlob" \
    -H "Content-Length: $(wc -c < "$2" | tr -d ' ')" \
    --data-binary "@$2" \
    "https://${account}.blob.core.windows.net/$1"
}
storage_get "raw/$input_blob" P001_img.nrrd

if [[ ! -x /mnt/skullbase-entry-benchmark/venv/bin/TotalSegmentator ]]; then
  python3 -m venv venv
  venv/bin/python -m pip install --quiet --upgrade pip
  venv/bin/python -m pip install --quiet "TotalSegmentator==2.11.0" "SimpleITK>=2.3"
  tool="$work/venv/bin/TotalSegmentator"
else
  tool=/mnt/skullbase-entry-benchmark/venv/bin/TotalSegmentator
fi
python_bin=$(dirname "$tool")/python
"$python_bin" - <<'PY'
import SimpleITK as sitk
image = sitk.ReadImage("P001_img.nrrd")
sitk.WriteImage(image, "P001_img.nii.gz")
PY

rm -rf craniofacial headneck_vessels
start=$(date +%s%N)
timeout 80m "$tool" -i P001_img.nii.gz -o craniofacial -ta craniofacial_structures -d cpu
craniofacial_ms=$(( ($(date +%s%N) - start) / 1000000 ))
start=$(date +%s%N)
timeout 80m "$tool" -i P001_img.nii.gz -o headneck_vessels -ta headneck_bones_vessels -d cpu
vessels_ms=$(( ($(date +%s%N) - start) / 1000000 ))

tar -czf anatomy-masks.tar.gz craniofacial headneck_vessels
sha256sum P001_img.nrrd anatomy-masks.tar.gz > SHA256SUMS
cat > timings.json <<EOF
{"schema_version":"real-ct-azure-anatomy-v1","case_id":"NasalSeg-P001","hardware":"Standard_D4as_v7 CPU","model":"TotalSegmentator 2.11.0","tasks":{"craniofacial_structures_ms":$craniofacial_ms,"headneck_bones_vessels_ms":$vessels_ms},"public_data_only":true,"clinical_validation":false}
EOF
for file in anatomy-masks.tar.gz SHA256SUMS timings.json; do
  storage_put "artifacts/$output_prefix/$file" "$file"
done
cat timings.json
