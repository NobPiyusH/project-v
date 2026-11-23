# GPU benchmark plan (written 20 Nov 2026)

Goal: find a cheaper, dependable home for Wan 2.2 clips and SeedVR2 4K upscaling than Modal's H100 (about ₹24 per 4-clip Short, about ₹371/hr), without lowering quality. Quality first, cost second (CLAUDE.md rule 10). Nothing here runs or spends without the owner's yes.

## Where things stand
- Modal's $30 monthly credit is used up for this period. Daily autopilot is PAUSED (reminder set for 1 Dec). Modal stays the dependable route for the daily Short.
- RunPod: account connected, empty (no pods, volumes, endpoints). Live stock was Low or none on every card we want (19-20 Nov). Plugin installed; its tools stay read-only until a benchmark is approved.
- Watchdog (rule 8) is merged, dry-run: `PodWatchdog`, `PodSelfStop`, migration V31. It must be tested against each provider before any real pod.

## Candidates (prices read on provider pages, 19-20 Nov; re-check before use, rule 7)
| Rank | Provider and card | Price | Why |
|---|---|---|---|
| 1 | Jarvislabs RTX PRO 6000 (96 GB, India) | ₹207.36/hr, per second | Full-quality Wan (no fp8) and SeedVR2 4K on one card; persistent storage; CLI; INR + GST. Min disk 100 GB (about ₹946/mo) |
| 2 | GridShare serverless RTX 5090 (India) | about ₹77/hr compute only, cold start free | Cheapest per Short if it works; /workspace and large files kept at ₹6/GB/mo. Unknown: weights handling, output size limits, stock |
| 3 | Salad RTX 5090, High tier | $0.60/hr | Cheap, plentiful stock; but wiped disk, 35 GB image limit, needs cloud storage for outputs |
| 4 | RunPod A100 80GB / L40S | about ₹112 / ₹74 | Only when in stock; both jobs on A100 |
| Skip | CloudPe L4, E2E (hourly billing), Vultr, QuickPod, Race, AIC, Spheron, AWS | | Slow, hourly-rounded, stale or pricier |

## Owner steps (his accounts and money)
1. Jarvislabs: create account, add about ₹500 wallet, add an SSH key. Add the API key to `.env` as `JARVISLABS_API_KEY` himself (never paste in chat).
2. GridShare (optional, free ₹25 trial): create account, verify email.
3. Tell Claude when each is ready. Claude never creates accounts or handles passwords or codes.

## Safety rules for every run
- Claude states GPU, ₹/hr, max runtime and the stop time before launching; the owner says yes per run.
- Cap per run: ₹150. Max lifetime 90 minutes on the pod itself (self-terminate wrapper) plus the control-plane watchdog.
- Only studio-named instances (`projectv-` prefix). Terminate (not just stop) when finished unless the owner wants the disk kept.
- Every run writes a CostEntry and RoutingDecision (rule 6). No secrets in logs.
- Read the provider's billing page after each run and compare with our ledger.

## Test matrix (one session, about ₹100-150)
Use one real Dream Short's 4 pictures and motion prompts, same seeds as the Modal baseline.
1. Baseline: Modal H100 clips (already have, or make once, about ₹24).
2. Jarvislabs RTX PRO 6000: Wan 2.2 fast 480p, 4 clips, full-quality weights.
3. Same card: SeedVR2 4K on one finished clip.
4. If step 2 is good and the owner wants cheaper: repeat on the cheaper card (GridShare 5090 or Salad 5090) with the fp8 Wan build.

## Measure and record
- Wall time to first clip (provider start, image/model load, first frame), per-clip render time, total billed seconds, ₹ per Short, ₹ per upscaled minute.
- Model download time on a fresh machine (35 GB fp8 and 127 GB full), and whether the disk persisted after stop and start.
- Peak VRAM, any out-of-memory or tiling for 4K.
- Failures: preemption, disconnects, API errors, how the watchdog behaved (dry-run log).

## Pass / fail
- Quality: owner compares clips side by side against Modal's by eye, blind if possible. fp8 only ships if he cannot tell, or prefers it. Otherwise use full weights or stay on Modal.
- Cost: at least 30% cheaper per Short than Modal after counting start-up and storage, or it is not worth the extra moving parts.
- Reliability: start and finish without manual fixes; watchdog can stop the instance.

## Decision after the test
- Wins: add the provider as an off-by-default router option behind a flag (CLAUDE.md rules 7 and 8), start with long films (one start-up, many clips), keep Modal as fallback; daily Shorts move only when it has run cleanly for a week.
- Loses: stay on Modal; revisit when Modal credits renew (1 Dec).

## Open questions to answer first (read-only)
- Jarvislabs: CLI stop/delete commands, shared filesystem price, which GPUs are actually launchable today (A100 not in the VM list).
- GridShare: serverless output size, weights persistence, 5090 stock.
- Salad: only if GridShare fails; needs R2/B2 for outputs and a lean image (under 35 GB) with startup download.
