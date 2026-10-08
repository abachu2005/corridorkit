#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 RESOURCE_GROUP BUDGET_NAME ESTIMATED_MAX_MONTHLY_USD" >&2
  exit 64
}

[[ $# -eq 3 ]] || usage
resource_group=$1
budget_name=$2
estimate=$3

command -v az >/dev/null 2>&1 || {
  echo "FAIL: Azure CLI is required; no resources were changed." >&2
  exit 1
}

[[ "$estimate" =~ ^[0-9]+([.][0-9]+)?$ ]] || {
  echo "FAIL: estimate must be a non-negative decimal amount." >&2
  exit 1
}

az account show --only-show-errors >/dev/null || {
  echo "FAIL: Azure CLI authentication is required; no resources were changed." >&2
  exit 1
}

scope=$(az group show \
  --name "$resource_group" \
  --query id \
  --output tsv \
  --only-show-errors) || {
  echo "FAIL: resource group is unavailable; no resources were changed." >&2
  exit 1
}

amount=$(az consumption budget show \
  --budget-name "$budget_name" \
  --scope "$scope" \
  --query amount \
  --output tsv \
  --only-show-errors) || {
  echo "FAIL: budget was not found at the resource-group scope." >&2
  exit 1
}

[[ "$amount" =~ ^[0-9]+([.][0-9]+)?$ ]] || {
  echo "FAIL: budget amount was not numeric." >&2
  exit 1
}

python3 - "$estimate" "$amount" <<'PY'
from decimal import Decimal, InvalidOperation
import sys

try:
    estimate, budget = map(Decimal, sys.argv[1:])
except InvalidOperation:
    print("FAIL: invalid decimal amount.", file=sys.stderr)
    raise SystemExit(1)

if estimate > budget:
    print(
        f"FAIL: estimate ${estimate} exceeds budget ${budget}; no resources were changed.",
        file=sys.stderr,
    )
    raise SystemExit(1)
print(f"PASS: estimate ${estimate} does not exceed configured budget ${budget}.")
PY

echo "NOTE: Azure budgets alert but do not enforce a spending shutdown."
echo "NEXT: run an Azure deployment what-if and obtain explicit approval."
