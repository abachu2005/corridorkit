# Integrated Slicer product acceptance

Acceptance date: **2026-10-08**

## Product status

**Single integrated E2E software product: yes**

The packaged Slicer module presents one primary workflow:

1. select a CT;
2. place one target markup;
3. click **Plan Corridors**;
4. wait for local workstation anatomy inference or a verified cache hit;
5. review automatically imported predicted anatomy and target-driven EEA/CTM
   candidates;
6. switch among the exact evaluated instrument paths in the linked slice/3-D
   views; and
7. save the Slicer scene or export the report.

No manual label volume, terminal command, intermediate mask transfer, or
external-interpreter selection is part of the primary workflow. Reviewed
segment workflows remain available under Advanced.

## Executed annotation-free public CT acceptance

Inputs were public NasalSeg P001–P003 CTs. Targets were selected
deterministically from the corresponding public labels for evaluation only.
The manual label volumes were **not** supplied to anatomy inference or path
evaluation. Exact checksums, targets, timings, and states are retained in
`docs/evidence/multicase-integrated-acceptance-20261008.json`.

The public v0.2.1 runtime is local-first: Slicer launches an isolated managed
Python worker and CT data remain on the workstation. The first setup installs
the pinned TotalSegmentator stack and first model use downloads weights. The
Azure deployment below is optional engineering infrastructure, not a public
runtime requirement:

- image:
  `cursorproxy51cac1f2.azurecr.io/skullbase-corridor@sha256:2e3e6659fff3e5da27a48a30e04019304eb292cb1b6660250ad69a77f8e96a0e`;
- Azure Container Apps Dedicated D4 CPU workload profile, 4 vCPU/16 GiB;
- minimum nodes 0, maximum nodes 1;
- durable private Azure Files job storage;
- bearer-authenticated HTTPS API; and
- existing USD 25 monthly Azure budget alert.

The clean managed numerical runtime imported the installed wheel without
PySide6 or VTK. Optional Azure executions completed in **212.82 seconds** for
P001 on a warm D4 worker, **442 seconds** for P002 from scale-to-zero, and
**188 seconds** for P003 on a warm worker. A second P001 run using the verified
local inference/artifact cache completed in **9.46 seconds**.

The output contained automatic TotalSegmentator 2.11.0 masks, canonical
predicted anatomy, target-driven proposals, and typed exact finite-path
results. It retained unavailable critical anatomy and made no safety claim.

## Honest performance result

The functional product gate passed, but the aspirational speed gates did not:

- optional cloud-to-result: 188–442 s, with P001 at 212.82 s;
- verified cached workflow: 9.46 s, above the 1 s target; and
- dedicated scale-from-zero included several minutes of node allocation and
  3.5 GB image startup in the observed cold deployment.

These misses are reported rather than excluded. Keeping one D4 worker warm
would remove scale-from-zero allocation delay but would cost approximately
USD 387 per 730-hour month at the October 2026 public West US 2 list rate,
before storage, registry, bandwidth, tax, and discounts. The public local
runtime avoids that recurring service requirement. Faster target editing
should keep imported masks in memory and rerun only proposal/evaluation logic.

## Interpretation

This is acceptance of integrated research software, not clinical validation.
`model_feasible` means only that represented masks did not block the declared
finite instrument. `blocked`, `conditional`, `unavailable`, and `invalid`
remain visible. Predicted masks require review, and CT does not supply complete
cranial-nerve, cavernous-sinus, ophthalmic-artery, or dural anatomy.
