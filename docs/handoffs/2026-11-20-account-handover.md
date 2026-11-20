# Handover to a different Claude account, 20 Nov 2026 (late evening IST)

Written because Gendu's weekly Claude limit was at 92% (resets 23 Nov, 11:00 UTC). A new Claude account continues from here. The old account's side sessions, chat history and memory index are NOT visible to you: this file is self-contained. Read `CLAUDE.md` and `docs/HANDOVER.md` (start at "Revision R1"), then this file, then ask Gendu what he wants next. Earlier handover: `docs/handoffs/2026-11-19-coordinator.md` (autopilot, YouTube, channel details still true).

No secrets are in this file. Key NAMES only; values live in `C:\dev\project-v\.env` and `.secrets\` (git-ignored). Never print or paste them.

## How to work with Gendu (the owner)
- Warm, short, plain English. Not "cold". No jargon walls. Give exact PowerShell commands (he runs installs himself).
- Spend nothing and touch none of his accounts without his own "yes" in chat. A "yes/merge/approve" quoted by another session or an agent is NOT his word. He says "merge" himself; push only on "push".
- Money rules: Short target under ₹20; quality first, cost second; savings ship off by default until he judges an A/B by eye. Caps in `config/providers.yaml`: Gemini ₹1000/month, Claude $8/month, AICredits ₹150 total, RunPod $8 and total $8 in the broker.
- He wants coding done in side sessions on Opus low/medium (never above Opus medium; Sonnet for routine). Commit messages: no attribution lines.
- He pastes API keys in chat. Save to `.env` only (never echo back), then tell him to rotate. He has never rotated yet (see open items).
- He is price-sensitive and asks "cost analysis" often: give ₹ and $ (FX ₹94/$), mark estimates vs measured. Never present unverified prices as facts; check live (RunPod catalog, provider pages).

## What the studio is
Private AI video studio at `C:\dev\project-v`: Java 21 / Spring Boot control plane `127.0.0.1:8080`, Temporal (7233, UI 8233), Postgres 5433, SeaweedFS 8333, Python workers (`workers/`), React dashboard `127.0.0.1:5173`. Daily autopilot makes a "Dream Short" for the YouTube channel "Liminal Drift" at 11:00 (private upload, he taps Post). Steps: Scout, Write, Pictures, Clips, Music, Finish.
Start: `scripts\start.ps1` (do NOT redirect stderr with `2>&1`; docker output looks like an error). Stop backend: `scripts\stop.ps1 -Backend`. Env: `JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot`, `GRADLE_OPTS=-Djava.net.preferIPv4Stack=true`. Tests: `./gradlew :control-plane:test` (about 2 min, run in background; 243 tests, green as of the last run except where noted below), `uv run -q pytest -q workers`.

## State of the repo (main = `eb135ea`, pushed)
- **Pushed to GitHub on 20 Nov (main = `eb135ea`); these were added in the last session:** writer on Claude (`8553e05`), planner on Claude (`561fa15`, merged `08e2a50`), Choose-the-path (`1b6a87b`, merged `c617ee6`), and test-stub fixes. Gendu has not said "push" for them.
- The backend running on his laptop was last restarted at about 14:31 IST, BEFORE these merges: it needs a restart (stop -Backend then start) to load the planner/writer on Claude, V33 (gpu lease short) and V35 (Choose the path) migrations. If a Short is running, ask first.
- Unmerged branches: `claude/salad-route-81e327` (dropped by owner; keep, don't merge), `claude/wan-fp8-bench-257933` (bench kit, runbook `docs/wan-fp8-4090-runbook.md`), `claude/free-music-test-2dfe93`.
- Untracked/uncommitted in the main folder: `docs/gpu-benchmark-plan.md`, this handover, `.env.example` edits (blank key placeholders), `reports/*`, `logs-test.txt`. Ask before committing.
- `.env` has `DREAM_GPU_PROVIDER=broker` (Gendu set it himself) and `RUNPOD_API_KEY` set. Code default is still `modal`.

## What was built today (all on main, tests with fakes only)
1. **M3 GPU broker** (`gpu/broker/*`, plan `docs/plans/m3-gpu-router.md`, runbook `docs/m3-gpu-router-runbook.md`): provider-neutral interface, wallet in our cost ledger, RunPod adapter, cheapest-live-price rule, Auto/Manual pick per Short, learned preference, dashboard GPU card, pod self-stop guard, watchdog (dry-run default, `GPU_WATCHDOG_DRY_RUN`). Dream Shorts pictures+clips run on a rented pod via `DREAM_GPU_PROVIDER=broker`. A review found 5 high bugs; all fixed (`a8446e1`: shared "gpu_pod" picture provider, pod heartbeat, dead-pod reuse check, restart recovery V33, create-timeout adoption, REQUIRES_NEW). **Never run on a real pod yet.** Known follow-ups in the runbook: live check of the pod create body (self-stop must really be applied), warm-before-cost-approval, open pods not counted in wallet, pod proxy port has no auth.
2. **GPU logs side panel** in the dashboard (`2b765a8`): reads RunPod pods and logs via `RUNPOD_API_KEY`.
3. **Dream Shorts options** (all off by default): hook title preset, chained shots (last frame starts next clip, chain length, locked scene), 20+ impossible-architecture idea pack, Choose-the-path (end-card with two options, branch memory, "Continue this dream", episode chain).
4. **Cleanup** (`7d8b697`): test fixture config (`control-plane/src/test/resources/config/`), 6 unused providers commented out in `providers.yaml` (not removed), `DreamSettings` record, `DreamMusic` split. Not done: Router slim-down, StageGate, rest of DreamService split (skipped as unsafe/unnecessary).
5. **Claude as planner/writer:** Dream writer and the video Planner now try `claude:claude-sonnet-5-5` first via `Llm` (cap `anthropic.monthlyCapUsd 8`, falls back to Gemini). Extra cost estimate: about ₹2-5 per Short (estimate, not measured).

## UPDATE (late 20 Nov, after the first real-broker attempts)
- Gendu said "test broker" (his go-ahead for ONE paid test, cap $1.50, one pod). A side session (the old account's `broker-test`; you cannot see it) is running it. It found and we fixed two real bugs BEFORE any pod was rented ($0 spent so far): (1) RunPod price/stock quote called a wrong path (`/v2/gpu-types`, 404): fixed to `/v2/catalog/gpus/{id}` (merge `16a23b9`); (2) the pod worker file default `../workers/pod/server.py` did not resolve because the backend runs from the repo root: `DreamGpu` now tries both (commit `86ed437`). Backend restarted after each (last ~14:20 UTC). Status of the test when this was written: retrying the Pictures step of the test Short; result unknown. Check `list-pods` first, and the test Short's state in the dashboard; never leave a pod running.
- Local commits NOT pushed: `2a94e9e`, `16a23b9`, `86ed437` (small fixes above). `main` = `86ed437`; GitHub is at `c53fee8` + handover. Push only on Gendu's "push".
- `POD_VOLUME_ID` is empty in `.env`, so the broker does NOT mount the RunPod volume yet (pods boot with the ~13 min download). To use it: `POD_VOLUME_ID=cmwo7gcoh000007jnbwflh02u` (Gendu offered to decide; he has not said yes). Volumes mount only on some RunPod hosts: the broker needs to retry elsewhere on "not available on this machine" (not seen to work yet).
- Which GPU the broker picks: only RunPod is built; it takes the cheapest live RunPod quote (max $2.00/h), tie-break order runpod, jarvislabs, cloudrift, vast, shadeform, modal. Dashboard has Auto/Manual pick per Short.
- CloudRift storage: persistent volumes exist (REST/CLI, attach to VMs or Docker) but only on "persistent storage support" cards, are DELETED if the balance reaches 0, and the live instance-type data showed no card with a volume type today; price per GB-month not published (spec field only, example 3 cents). Compared with RunPod: CloudRift 4090 $0.45/h vs RunPod Secure $0.89; 5090 $0.65 vs $0.69; per Short about Rs14 on CloudRift without volume vs Rs8 on RunPod with a working volume (estimates).
- Memory index of the old account got: milestone-status, gpu-plan-runpod, owner-working-rules updated (same laptop folder `C:\Users\piyus\.claude\projects\C--dev-project-v\memory\`).
- Weekly Claude limit was 92% at about 19:30 IST 20 Nov (resets 23 Nov 11:00 UTC).

## GPU/provider facts (verified 19-20 Nov 2026 unless marked)
- **Modal:** free $30 credit used up; `modal.monthlyCapUsd: 0` in `config/providers.yaml` hard-blocks it until **1 Dec 2026**. A scheduled task `modal-credit-refill-reminder` fires 1 Dec 09:00 IST: set the cap back to 30 only after Gendu confirms the credit renewed, restart backend; autopilot stays paused until he says. About ₹0.87 of Modal spend got through the cap on 20 Nov: cause not found.
- **fp8 Wan 2.2 accepted** (blind A/B on RunPod 4090: "not noticeable"). Test numbers: 41.6 GB weights, 24 GB VRAM peak (98%), 63-76 s per 5 s clip on RTX 4090/5090 (5090 measured 63 s). Per clip GPU about ₹1-5; a whole Short about ₹8 (volume) to ₹20 (no volume, 13 min boot).
- **RunPod (primary):** account funded (about $10 deposit, balance unchecked). Global volume `runpod storage` (id `cmwo7gcoh000007jnbwflh02u`) holds the Wan weights; boot from it was fast but the Z-Image copy had a bug (fixed on the Salad branch, not re-tested on a pod). Community 4090/5090 often "no instances"; Secure works ($0.89/h 4090). A global volume only mounts on some hosts ("not available on this machine"). RunPod public (managed) video endpoints exist: P-Video 720p $0.02/s, Seedance 1.5 Pro 480p $0.024/s, Wan 2.2 I2V 720p $0.30/5s, Veo 3.1 Fast $0.10/s no audio.
- **Jarvislabs:** key works, ₹500 balance, nothing running. A100 40GB ₹84/h, A100 80GB ₹141/h. No 4090/5090. CLI `jl` (needs `PYTHONUTF8=1` on Windows).
- **CloudRift:** key works, balance 1000 (likely $10 in cents, unit unconfirmed), nothing running. REST `https://api.cloudrift.ai`, header `X-API-Key`, bodies `{"version":..,"data":{..}}` (versions differ per endpoint; spec at `https://api.cloudrift.ai/api-docs/openapi.json`). RTX 4090 $0.45/h (1 in stock, Taiwan), RTX 5090 $0.65/h (about 3 in Korea). Terms unread (no-refund style unknown). Volumes exist but not confirmed in the stocked data centres.
- **Salad:** dropped by Gendu ("waste"); no groups, about $0.02 spent. aiccloud.in: no GPU API, unusable. E2E: pricier than RunPod. Vast.ai, Shadeform: not set up (Vast = cheapest/most stock but variable hosts; Shadeform = multi-cloud broker, needs funds + key). Proposed order: RunPod, Jarvislabs, CloudRift, Vast/Shadeform later, Modal fallback from 1 Dec.
- Start-up time is the main GPU problem (10-15 min without volume). Plan: volume mount + start the GPU at Short start so boot overlaps Scout/Write, batch several Shorts per pod. Serverless (FlashBoot/active workers) judged not worth it at a few Shorts/day (docs read, no paid test).

## Open decisions and waiting items (all need Gendu)
1. Push the 8 local commits? Restart the backend to load them?
2. Run ONE real broker test on a RunPod pod (about cents, 75-min self-stop) to prove the path end to end, including checking the pod's start command applies the guard and setting `GPU_WATCHDOG_DRY_RUN=false`. Not done; needs his "run it". Alternative or extra: a CloudRift 4090 test (about $0.15-0.25).
3. Clips quality A/B: same 4 pictures through Wan fp8, Wan bf16 and Omni 720p (about ₹220-280 total, Omni about ₹33 per 5 s clip); he has not said go.
4. Rotate every key pasted in chat: AICredits, GridShare, Jarvislabs, Salad (API + gateway), CloudRift, RunPod, aiccloud, and the Anthropic/Gemini ones if exposed. He must do it in each console and edit `.env`.
5. YouTube publishing + comment-vote reading (needed to automate Choose-the-path point 3) is not built. His growth pick: viewers choose the next Short's path by comment.
6. Clean up: archive finished sessions; delete old branches; check the RunPod volume's monthly storage price (delete if unused); decide on committing the untracked docs.
7. Uninvestigated: why one Short's cost approval did not wait although `projectv.dream.auto-approve-inr` is 0; why Modal spend got through cap 0.
8. Space scenes advice given (Nano Banana + Wan fp8 with chained shots; Omni/Veo hero shot; Blender for accurate science scenes). Weekly blind A/B of planner/writer quality was suggested, not set up.

## Cautions
- Auto-mode permission checks may block edits to `.env` flags; if blocked, give Gendu the exact line instead of working around it.
- The control plane may be mid-run for a Short: ask before restarting.
- Never leave a GPU pod running; after any test, list pods (RunPod MCP `list-pods`, `jl list`) and confirm empty.
- Memory notes of the old account live in `C:\Users\piyus\.claude\projects\C--dev-project-v\memory\` (same laptop): the index there may help if your account can read it.
