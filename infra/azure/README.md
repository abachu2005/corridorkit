# Optional Azure inference deployment

This directory is a deployment template, not evidence that an Azure benchmark
or deployment has been run. Nothing here provisions resources automatically.
The Python provider is fully usable with its offline deterministic fake.

## HTTP contract

Configure the client at runtime:

```text
AZURE_ML_ENDPOINT_URL=https://<host>/<base-path>
AZURE_ML_ENDPOINT_TOKEN=<secret supplied out of band>
```

Never commit either value. The service behind the URL must implement:

- `POST /jobs` -> `{"job_id": "...", "cache_key": "..."}`
- `GET /jobs/{job_id}` -> `{"job_id": "...", "state": "queued|running|succeeded|failed|cancelled"}`
- `DELETE /jobs/{job_id}` -> the same status shape
- `GET /jobs/{job_id}/result` -> the `InferenceResult` JSON schema in
  `src/skullbase_corridor/anatomy/azure_provider.py`

The service must treat the `Idempotency-Key` header as unique and durable.
Repeated submissions with the same key must return the original job instead of
starting duplicate GPU work. Do not include authorization headers, source
image bytes, signed artifact URLs, or patient identifiers in logs.

Although the client class is named `AzureMLHttpProvider`, the asynchronous API
should normally be a thin HTTPS job gateway. It can enqueue an Azure ML batch
job and return immediately. Keeping GPU compute in a batch job avoids an
always-on managed-online-endpoint replica.

## Cost and shutdown controls

1. Run `./cost-cap-preflight.sh <resource-group> <monthly-budget-name>
   <estimated-max-monthly-usd>` before deployment. It is read-only and fails
   closed if the Azure CLI is unauthenticated, the budget is absent, or the
   supplied worst-case estimate exceeds the budget.
2. Review `gateway.bicep`; deploy only after an owner approves the Azure quote.
   It uses Azure Container Apps Consumption with `minReplicas: 0` and
   `maxReplicas: 1`. Scale-to-zero is not an instantaneous hard stop and does
   not eliminate storage, registry, logging, egress, or job-compute charges.
3. Configure the backend batch compute with zero minimum nodes and a short idle
   timeout. Do not use a GPU managed online endpoint unless a nonzero idle cost
   is explicitly approved; managed endpoint scale-to-zero support depends on
   the selected Azure ML SKU and current Azure capabilities.
4. Add a real Azure Cost Management budget and alerts before deployment.
   Budgets alert; they do **not** stop resources. Use an Azure Policy/automation
   shutdown action as an independent control, and test it on nonclinical data.
5. Keep source images and outputs in private storage, use short-lived managed
   identity access where possible, rotate the gateway token, restrict ingress,
   and set a short data-retention policy.

Example dry-run and deployment (these commands may still query Azure):

```bash
./infra/azure/cost-cap-preflight.sh rg-name monthly-budget 25
az deployment group what-if \
  --resource-group rg-name \
  --template-file infra/azure/gateway.bicep \
  --parameters containerImage='<approved-image-digest>' endpointToken='<secret>'
# Run `az deployment group create ...` only after reviewing the what-if and quote.
```

The template deliberately accepts an immutable image reference and a secure
token parameter. For production, source the token from a protected CI secret or
Key Vault integration; never place it in a parameter file or command history.

## Observed public-atlas baseline (2026-10-07)

`run-public-atlas-cpu-benchmark.sh` was executed on the existing
`Standard_D4as_v7` East US research VM, using TotalSegmentator 2.11.0
`head_glands_cavities` and the checksum-verified UW averaged atlas. The observed
cold run produced 19 masks: inference was 151,192 ms and the instrumented total
was 163,580 ms. The worker was deallocated after the run.

This is CPU feasibility evidence only. It is one observation, has no matching
task ground truth, and does not establish anatomical quality or the two-minute
warm-cloud goal. The specialist task rejected TotalSegmentator's `--fast`
option, so reduced-resolution mode is not enabled for this task. GPU testing
was not run because all queried NC/NV family quotas were zero. The raw Azure
response, extracted JSONL sample, and aggregate report are under
`research/results/azure-entry-benchmark-20261007/`.
