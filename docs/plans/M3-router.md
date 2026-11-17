# M3 plan: the router (draft for the owner's approval, 18 Nov 2026)

HANDOVER §23, M3: "Full router (scoring, quotas, resets, budget guard, deferral, fallbacks), providers: Modal,
Kaggle, Vast, RunPod, local, Gemini API, manual Flow. Pod session manager with auto-shutdown; interruption
handling. Router and budget dashboard. **Done when:** a film runs with free quotas exhausted mid-batch and the system
switches providers automatically, stays under the budget cap, and logs every decision."

Nothing in this plan is built until the owner approves it. Paid tests are asked for separately.

## Where we start from (already built)
- Fallback lists per stage (text: Claude → Gemini → NVIDIA/Groq/OpenRouter; voice: ElevenLabs → Gemini → Kokoro;
  research: Claude → Gemini; smoothing: laptop GPU → Modal), fail-fast on overload (18 Nov).
- Budget caps that stop spending: Gemini ₹400, GPU (Modal) monthly cap, Claude $8; cost gates per stage.
- Tables from M1, still empty: `provider` (9 rows), `provider_account`, `quota_snapshot`, `routing_decision`.
- What is missing: one place that decides *which provider runs a job and why*, live quotas, deferral, the pod
  manager, and the logs/dashboard. Today each stage hard-codes its own order.

## Phases (each ends with a demo and the owner's OK)

### M3a: Router core (≈1.5–2 weeks part-time). No new accounts, no new spending.
1. **Provider registry** filled from `config/providers.yaml`: capabilities (text, image, video, upscale, smooth,
   voice, render), price source, speed estimates, quotas and reset rules (rule 7: prices from config or live).
2. **One `Router.choose(job)`** used by every paid stage: candidates → filter (capability, budget left, quota left,
   provider healthy, owner's per-video preferences) → score → choose.
   - Score = quality weight (always first, owner rule) × expected success × (1 / cost) × (1 / wait).
   - Quality tiers come from our A/B results (e.g. Claude research > Gemini Flash-Lite; SeedVR2 fit = padded).
3. **Health:** a provider that failed recently (503s, timeouts) is skipped for a cool-down, then retried.
4. **Quotas and resets:** per account, units used vs total (`quota_snapshot`); free tiers used first when quality
   is equal; `exhausted_until` set when a provider says "quota used up".
5. **Deferral:** when every provider for a job is over budget or exhausted, the stage waits ("deferred until
   <reset time>") instead of failing; the owner sees why and can override.
6. **Every decision logged** (rule 6): `routing_decision` with candidates, scores, chosen, reason, owner override.
7. Existing fallback lists become router candidates; behaviour stays the same until scores are tuned.
8. **Tests:** quota exhausted mid-batch → next provider; budget cap → deferral; owner override respected;
   deterministic workflow (decisions made in activities, results passed as data).

### M3b: Router and budget dashboard (≈3–5 days)
- Per provider: health, quota used/left, spend this month, next reset.
- Per video: every routing decision with its reason; "why this provider?" on each stage.
- Owner controls: disable a provider, prefer one, set per-provider monthly caps.

### M3c: Pod session manager (≈1–1.5 weeks). Only if the owner approves Vast.ai/RunPod (accounts, spending).
- Rent → load models from Hugging Face → run a **batch** → copy results → **destroy**. Verified datacenter hosts only.
- Watchdog (rule 8): a pod idle for N minutes, or past its budget, or orphaned after a crash is destroyed; a
  scheduled sweep lists every pod on the account and kills anything not owned by a running job. A forgotten pod is
  treated as a critical bug.
- Interruption handling: checkpoint per clip; a vanished host loses only the clip in progress; retry elsewhere.
- First target: long SeedVR2 batches (the biggest GPU line), with Modal as the fallback.

### M3d: Other providers from §23 (as the owner decides)
- **Manual Google Flow** (rule 4, no automation): the studio writes a shot list; the owner generates in Flow and
  drops files in a watched folder; the router treats it as a slow, free provider.
- **Kaggle** free T4 (images/upscale), within Kaggle's terms; only if it proves useful.

## Acceptance demo (HANDOVER's test)
A Short is made while: the Gemini free quota is exhausted mid-keyframes, Modal's monthly cap is nearly used, and one
provider is failing. The studio switches providers by itself, never passes a budget cap (defers instead), and the
dashboard shows every decision and why.

## Costs
- M3a/M3b: no new spending beyond normal video runs.
- M3c: Vast.ai/RunPod tests ≈ ₹100–300 (owner approves first); a Vast.ai account created by the owner.

## Risks
- Over-engineering: keep scoring simple and explainable; every rule must show its benefit on the ledger.
- Pod billing (M3c): the watchdog and the sweep are built and tested before any real rental.
- Determinism: routing runs inside activities; workflows only see the chosen provider as data.

## Decisions needed from the owner
1. Approve M3a + M3b now (no new accounts or spending)?
2. M3c (Vast.ai/RunPod): later, when you decide ("will tell later").
3. M3d: Manual Flow drop folder — wanted? Kaggle — wanted?
