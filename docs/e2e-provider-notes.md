# E2E Networks as a GPU provider: findings (22 Nov 2026)

Sources: docs.e2enetworks.com (MyAccount docs and the OpenAPI at `/api/myaccount/openapi.json`) and **read-only** calls with the
keys in `.env` (node list, GPU plan list, infra credit, resource quota, security groups). Nothing was created or spent.
FX used: **₹94 = $1**; GST **18% extra** (not in the plan prices below, as far as the API shows).

## Verdict
An SSH-free start **is** possible, so the adapter is built (`E2eProvider`). It is a **VM, not a container**: a start script
(cloud-init, runs as root on first boot) starts Docker with the studio's worker image. Nothing in the create call needs SSH.
**Not yet proven live**: no node has been created (needs the CTO's go).

## API (base `https://api.e2enetworks.com/myaccount/api/v1/`)
Auth: `Authorization: Bearer <E2E_AUTH_TOKEN>` plus `?apikey=<E2E_API_KEY>&location=Delhi` on every call (the key is in the
query string: never log URLs). `project_id` is documented as required; the list calls work without it.

| Need | Call |
|---|---|
| Plans with ₹/hour | `GET images/?os=Ubuntu&category=GPU&location=Delhi` (229 rows: one per card x image template) |
| Create | `POST nodes/` body: `name` (3-50, letters/digits/hyphens), `label`, `plan`, `image`, `ssh_keys` (**required**, >= 1), `number_of_instances` (1), `start_scripts` (array of shell scripts), `default_public_ip: true`, `disable_password: true` |
| Show / list | `GET nodes/{id}/`, `GET nodes/` (status, public_ip_address, plan, created_at) |
| Delete | `DELETE nodes/{id}/` |
| Power | `PUT nodes/{id}/actions/` `{"type":"power_off"}` etc. |
| Balance / quota | `GET pre-transaction/` (infra credit), `GET resource-quota-details/` |
| Firewall | `GET security_group/` (default group allows inbound 22, 80, 443, 1167, 3389, 10050 from anywhere) |

## Start without SSH
- `start_scripts`: array of shell scripts, **run via cloud-init runcmd as root, each max 16 KB**, failures are logged to
  `/var/log/cloud-init.log` and do not stop boot.
- `ssh_keys` is mandatory. The adapter generates an Ed25519 key pair in memory per process, **throws the private key away**
  and sends only the public half, with password login disabled: the box is unreachable by SSH for everyone.
- The worker payload (`workers/pod/server.py`, compressed + base64) is about 20 KB, over one script's 16 KB. The adapter writes the
  env file over several start scripts (each under 15 KB), then a last script runs `docker run`. The 16 KB limit per entry is from the
  OpenAPI text; **verify on the first real node** that several entries are all accepted.
- Ubuntu **22.04** GPU images ship Docker + NVIDIA Container Toolkit; 24.04 does not. The adapter uses 22.04.

## Public IP and ports
`default_public_ip: true` gives an auto public IPv4. The default security group already opens **80 and 443** from anywhere, so the
adapter publishes the worker on host port **443** (`-p 443:8000`), with no firewall change. The worker is protected by the per-rental
bearer token (same as Vast); the SV_TOKEN sits only in a root-only env file on the node.

## GPU plans, ₹ per hour (live, 22 Nov 2026; GST extra)
| Card (plan SKU) | vCPU / RAM / disk | ₹/h | ~$/h | ~$/h with 18% GST |
|---|---|---|---|---|
| L4 24 GB `GDC3.L4-25.110GB_v1` | 25 / 110 GB / 250 GB | 49 | 0.52 | 0.62 |
| A30 24 GB `GDC.A30-16.90GB` | 16 / 90 / 640 | 90 | 0.96 | 1.13 |
| A40 48 GB `GDC3.A40-16.100GB` | 16 / 100 / 750 | 96 | 1.02 | 1.20 |
| **L40S 48 GB `GDC3.L40S-60.220GB_v1`** | 60 / 220 / 250 | 102 | 1.09 | 1.28 |
| A100 80 GB `GDC3.A10080-16.115GB` | 16 / 115 / 250 | 180 | 1.91 | 2.26 |
| T4 16 GB, V100 32 GB | | 30 / 100 | | too small / old |

No H100/H200 on MyAccount GPU nodes (they are on TIR, a different product) and **no RTX 4090** class. Multi-GPU plans exist (2x, 4x, 8x)
but the adapter only offers 1-GPU plans. Plans list **no stock information**: availability is known only when a create succeeds or
fails, so the adapter quotes every allowed plan and the broker tries the next one on a failed create.

## Billing
- **Per minute**, at the hourly price, no minimum charge (on-demand). Committed plans (30/90/183/365 days) are prepaid: never used.
- **Power off does NOT stop billing** ("a powered-off node is still billed until it is Terminated"; the Stop action is E1-series
  only). Only **Delete** ends it, so teardown is `DELETE nodes/{id}/`, never power off.
- Delete is refused while the node is still *Creating* or another action runs: the adapter retries and the watchdog retries.

## Prerequisites (checked read-only)
- Infra credit: **₹2,000** (a promotional coupon, ~$21; spent from this). `gpu_and_higher_plans_enabled: true`: GPU plans are allowed.
- Resource quota: capped at 10 nodes, 0 used. The node list is empty.

## Cheapest suitable GPU (>= 24 GB VRAM, >= 48 GB RAM, as the studio spec asks)
**A40 48 GB, `GDC3.A40-16.100GB`, ₹96/h** (= $1.02/h; $1.20/h with GST). A ~25 min test: ₹40 = **$0.43** before GST,
**₹47.2 = $0.50** with GST. The adapter sorts quotes cheapest first, so A40 is tried before L40S (₹102/h, 60 vCPU, faster CPU side).
The L4 (₹49/h) is not in the allowed plan list (24 GB is the bare minimum and 110 GB RAM is fine, but it is untested for SVI).

## Adapter status (22 Nov 2026)
`E2eProvider` + `E2eConfig` + `E2eProviderTest` (fake server) are in; `./gradlew :control-plane:test` passes (331 tests). Config:
`pricing.gpuBroker.providers.e2e` in config/providers.yaml (monthly cap $5, plans, INR rate, GST); `order` lists `e2e`; `.env.example`
has `E2E_API_KEY`, `E2E_AUTH_TOKEN`, `E2E_PROJECT_ID`. `enabledByDefault` is false until a real node has run.
`dailyCapUsd` is still 5.00: the $10 approval reached this session only second-hand, so the owner/CTO must raise it themselves.

## Price per test and recommendation
SVI resume test, ~25-35 min on a node (boot + image pull + weights ~42 GB + run); billing runs from create to delete:
- **L40S (48 GB), recommended**: ₹102/h = $1.09/h; 30 min = ₹51 = **$0.54, $0.64 with GST**. Plenty of VRAM, 60 vCPU / 220 GB RAM, fast.
- L4 (24 GB): ₹49/h; 30 min = ₹24.5 = $0.26 ($0.31 with GST). At the 24 GB minimum, slower; fine for a smoke test of the adapter.
- The $0.45 SVI budget covers about 25 min of L40S before GST: tell the CTO it is tight; a first adapter smoke test on the L4 (~$0.15 for
  15 min) is cheaper.

## Unknowns to settle on the first node
1. Exact `status` strings (documented: Creating, Running, Stopped, Pending) and how long a GPU node takes to reach Running.
2. Whether several `start_scripts` entries are all honoured and the 16 KB limit applies per entry.
3. Whether the `ssh_keys`/`project_id` fields behave as documented (project_id is sent when `E2E_PROJECT_ID` is set).
4. Whether the plan price shown includes GST (the API gives no tax field).
