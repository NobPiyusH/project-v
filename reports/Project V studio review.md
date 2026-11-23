# Ship the first video, then harden

*Review of Project V, 18 Nov 2026. Covers the codebase, costs and providers, channel strategy, and roadmap and process. Sources: a read-only look at the repo at commit 91e4080, official price pages, YouTube Help pages, and press and vendor data (flagged where weaker).*

**Bottom line:** Gendu, the studio engine is in better shape than most solo projects ever reach. In three days you built about four milestones' worth of the plan, the GPU auto-shutdown works, every paid step checks a budget first, and secrets have never been committed. The weak spot isn't the engine. **Nothing has been published, so the 12-month YouTube Partner Program clock hasn't started and the learning loop has no audience data to learn from.** The second risk is that all your work lives on one laptop with no backups. The third is a handful of money-safety gaps in the code: the control plane is reachable from the local network, two runs can both pass the budget check at once, and a paid call can be retried and charged twice. My advice is to stop adding providers and formats for a few weeks, publish by hand, back everything up, close the money gaps, and only then return to the router (M3). A Short costs about ₹200–270 today and can drop to roughly **₹130–180 without touching quality**. At your planned pace, though, the current ₹1,000 Gemini cap only covers about 12–15 Shorts' keyframes, so the cap and the pace don't fit together yet.

## Ranked action list

| # | Do this | Why | Effort | Level |
|---|---|---|---|---|
| 1 | Choose channel names, create the Science channel, and **upload the approved Short(s) by hand** in YouTube Studio, ticking "Altered content" on photoreal shots | Starts the YPP and learning clocks; needs no code | 1 evening | CRITICAL |
| 2 | **Back up nightly**: `pg_dump` plus an rclone sync of SeaweedFS and the finished masters to Drive; then do the SSD move | Today, every Provenance row, cost row and asset sits on one laptop's C: drive | 1–2 days | HIGH |
| 3 | Set `server.address=127.0.0.1` and add a shared-secret header on `/api/internal/**` | Anyone on the same Wi-Fi can start spend-triggering runs or fake billing data | 1 hour | HIGH |
| 4 | Add tests for BudgetGuard, Pricing and CostLedger, plus "every clip writes a cost row" | The code paths that move money have no tests | 1–2 days | HIGH |
| 5 | Turn on the quality-neutral savings: **Batch tier for keyframes (−50%)**, Claude prompt caching, and Haiku for small text steps | Same models, so no A/B needed; ₹70–90 saved per Short | 2–3 days | HIGH |
| 6 | Decide the Gemini cap: about **₹2,500–3,000/month** for your pace, or fewer hero shots | Your owner decision; today's cap and pace targets contradict each other | 5 min | HIGH |
| 7 | Reserve budget at check time; stop Gemini failover on 5xx for paid media calls; reconcile the Modal bill against the ledger | Closes the overspend and double-charge gaps | 3–4 days | MEDIUM |
| 8 | Reorder the roadmap: **minimal M5** (publishing kit plus a retention fetch) → **M3-lite** (log RoutingDecision rows) → new formats one at a time, gated on channel data | Learning needs real viewers; the full router optimises providers you barely use | plan change | MEDIUM |
| 9 | Freeze new providers and models until cost per video stays stable across 5 videos | Cost and look change almost daily | discipline | MEDIUM |
| 10 | Housekeeping: normalise line endings once, refresh the stale hand-off and HANDOVER drift, delete `ltx.py`, add a workflow replay test | Cuts repeat churn and confusion | 1–2 days | LOW–MEDIUM |

The detail behind each item follows.

## The engine is solid, but three money paths leak

The architecture matches the plan: Temporal workflows, a Postgres ledger, Python workers, Modal GPU apps and a React dashboard, with the departures from the original design recorded in ADR-0001. Several things are done properly. **Every Modal app shuts down 30 s after its last call and has a hard timeout**, which meets rule 8 ([wan.py](../workers/modal_apps/wan.py), [zimage.py](../workers/modal_apps/zimage.py)). Every paid stage calls `budget.require` before it spends and `ledger.record` after ([BudgetGuard.java](../control-plane/src/main/java/com/projectv/ledger/BudgetGuard.java)). The `.env` file has never been in git. The broad error handlers in the Python code each carry a stated reason. There are about 73 Java tests and 57 Python tests.

Three gaps matter because they involve money. First, **the Spring control plane has no `server.address` and no security**, so on café or hostel Wi-Fi anyone who can reach port 8080 can start videos, approve stages or post fake billing data ([application.properties](../control-plane/src/main/resources/application.properties), [ApiController.java](../control-plane/src/main/java/com/projectv/api/ApiController.java)). Windows Firewall probably blocks this, but that isn't guaranteed. The one-line fix is cheap. Second, **the budget check is check-then-act with no reservation**. A redo running alongside a main run can both pass the same remaining headroom and overshoot the GPU cap by up to one batch, which is up to four H100s for an hour. Third, **the Gemini pool retries on the next project after a 500/502/503** ([gemini_pool.py](../workers/gemini_pool.py)). For video, music or image generation, a 503 can arrive after Google has already done the work, so you pay twice but record it once. The same risk applies when a paid Temporal activity retries after the provider has charged but before the ledger write. The Modal billing feed that could catch this is display-only and is lost on restart.

The smaller items: the `routing_decision` table exists but no code writes to it, even though rule 6 asks for one row per routing choice. Embabel isn't used, and ADR-0001 should say so. The main workflow has 10 `getVersion` gates and no replay test, and no procedure for retiring old gates. Five of 68 commits exist only to restore line endings. `ltx.py` is unused and could be deployed by accident.

My opinion: none of these are design flaws, and none need a rewrite. They are the normal debts of building very fast. **One hardening pass of about a week before M3 clears all of the HIGH and MEDIUM items.**

## A Short can cost ₹130–180 without touching quality

Your configured prices mostly match the official pages. One is stale: Gemini 3.6 Flash text is now **$0.75/$3.75 per million tokens and doubles on 1 Jan 2027**. Flash TTS doubles on the same date ([Gemini API pricing](https://aistudio.google.com/docs/pricing)). Text and TTS are only fallbacks for you, so the impact is small, but the fallback should move to Flash-Lite or Haiku.

| Stage (60 s Short) | Today, ₹ | Optimised, same models, ₹ | How |
|---|---|---|---|
| Research, script, shot plan (Sonnet 5.5) | 15–30 | 8–15 | Cache the style prompt; Haiku 5.5 for captions and metadata |
| Keyframes (Nano Banana 2.1) | 55–70 | 28–35 | **Batch tier, half price**, in Auto mode only |
| Music (Lyria 3.5) | 7–14 | 7 | One song, extended in the edit |
| Wan drafts (Modal H100) | 25–30 | 20–25 (₹0 cash inside the free credit) | Batch in one warm container |
| Omni hero shots, 3 × 5 s at 720p | ≈92 | 66–92 | A/B against Veo 3.1 Lite first |
| Smoothing and upscale | 10–25 | same | — |
| **Total** | **≈200–270** | **≈130–180** | |

The unit prices come from Google's official page: NB 2.1 keyframes at **$0.0336 standard vs $0.0168 batch**, Omni at about $0.07/s at 720p, Lyria at $0.08 a song ([Gemini API pricing](https://aistudio.google.com/docs/pricing)). Claude prices come from Anthropic's page: Sonnet 5.5 at $2/$10, with cache hits at $0.10 and Haiku 5.5 at $0.10/$0.50 ([Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing)). Batch, caching and warm containers produce the same output from the same model, so they carry no quality risk under your "quality first" rule. Model swaps (Z-Image, Nano Banana 2 Lite, Veo Lite) still need your A/B. Batch has one catch: it can take hours, so it suits Auto mode, not Manual retakes.

On GPUs, a RunPod H100 is about **half of Modal's $3.95/h**, and a 4090 is under a tenth ([RunPod](https://www.runpod.io/pricing), [Modal](https://modal.com/pricing)). At your current ~$18/month, everything fits inside **Modal's $30 free credit**, so switching saves nothing today. It starts to pay once long-form pushes you past the credit. A 10-minute film costs roughly ₹1,100–1,700 at list prices, or ₹800–1,200 optimised. Shorts cut from a long-form cost only ₹10–40 each, because they reuse its shots.

Monthly, your current pace (2–3 Shorts/week on two channels, about 22 a month) comes to roughly **₹2,700–4,500 optimised**. A long-form-first plan (2 films plus 10 cut-down Shorts) costs about the same, ₹2,500–4,000, and fits YouTube's rules better. Either way, **the ₹1,000 Gemini cap is too low for the plan**, so you either raise it to about ₹2,500–3,000 or keep hero shots rare.

On provider risk, Google now supplies your keyframes, music, hero video and both fallbacks. That's a lot of eggs in one basket: prices are scheduled to rise, and models are being rerouted quietly (Omni 1.1 moves to 1.2 on 3 Dec, and Gemini 2.5 Flash moves to Flash-Lite on 1 Dec) ([Gemini API pricing](https://aistudio.google.com/docs/pricing)). A silent model swap changes the look of your videos without any code change. Pin model IDs, alert when the served model differs from the one you asked for, and keep the open-weight path (Wan, Z-Image, SeedVR2, Chatterbox) ready to run on two GPU vendors. Your config already notes that Google's real bill ran about **×1.5 above the ledger**, so reconcile monthly against the billing export.

One compliance note, offered as a risk and not a demand. You removed rule 4 on 18 Nov, which was your call. The multi-project Gemini failover could look like quota spreading under Google's terms, and if Google read it that way, the likely result is suspended keys in the middle of a run. That is a LOW–MEDIUM business risk worth knowing about. Separately, any API key that was ever pasted into a chat is worth rotating.

## YouTube rewards a human-directed, varied channel, not volume

Using AI is allowed and can be monetised. What YouTube removes is "inauthentic" content, which it describes as including content "made with a template" ([YouTube Help](https://support.google.com/youtube/answer/1311392)). Press reports say enforcement now looks at the whole channel, across roughly the last 30 uploads ([The Next Web](https://thenextweb.com/news/youtube-ai-slop-crackdown-faceless-creators-collateral-damage?type=AI)). YouTube terminated or hid at least 18 AI-slop channels in January 2026 ([Tubefilter](https://www.tubefilter.com/2026/01/29/youtube-ai-slop-channel-crackdown-bans/amp/)). Detection also makes mistakes: Kurzgesagt's human-made videos were reportedly flagged ([Kotaku](https://kotaku.com/youtube-mistakenly-penalizes-popular-science-channel-kurzgesagt-for-ai-generated-slop-2000722702)). So your risk isn't any single video. It's a run of uploads that look alike: same intro, same voice cadence, same music bed, same 60 s shape.

The money also favours long-form. In the best dataset I found (274 channels), Shorts earn only **3–14% of long-form RPM**. **Education/Science has the highest RPM of any niche**, and channels with more than half their uploads as Shorts gained fewer subscribers ([AIR Media-Tech](https://air.io/en/air-data-findings/youtube-shorts-rpm-vs-long-form-how-much-do-shorts-earn-in-2026)). That data comes from a vendor and shows correlation, not cause, but it points one way. The realistic YPP path is **1,000 subscribers plus 4,000 long-form watch hours**; Shorts views don't count toward the hours ([YouTube Help](https://support.google.com/youtube/answer/72851)).

I found no successful AI-made science documentary channel to copy. MelodySheep and Kurzgesagt are human-made brands built on one person's style. That leaves a gap for a clearly human-directed AI studio, and your Science + Dreamscapes plan can fill it with a few adjustments:

- **Launch Science first, long-form-led:** about 1 film a week (8–15 min), each cut into 3–4 Shorts.
- **Start Dreamscapes 2–3 months later**, with Next Station as the flagship. Its recurring character and train framing are a real differentiator. Hold back the long ambient loops, which look the most template-like.
- **Show your authorship:** a "written & directed by" credit, sources in the description, your own Chatterbox voice, an original score each episode, and an occasional behind-the-scenes post.
- **Never pass AI imagery off as real footage** (a "real photo" of an exoplanet, say). That is a bigger risk than demonetisation.
- **On Instagram, post your own native vertical cuts.** Your originals are safe there; Instagram penalises reposted content, not first-party work ([PetaPixel](https://petapixel.com/2026/04/30/new-instagram-policies-target-reposted-content/)).

## Speed isn't your bottleneck; decisions and distribution are

The plan estimated 9–13 weeks for M1 and M2. Those milestones landed in 3 days, across 68 commits. That's impressive, but it reveals where the real constraints are: your review time, your decisions and real-world feedback. Three process symptoms show it. The M2 done criterion (a 3–5 minute film, a Premiere import and a 4K master) was approved on a 60 s 1080p Short. The docs have drifted: HANDOVER still says MinIO and Boot 3.5, and the hand-off note is about 50 commits old. And several features (Omni, Chatterbox, Freesound, the LTX test, the failover) landed outside any milestone.

The roadmap order is my main disagreement. M3 (the router) and the new formats roadmap put roughly 10–12 weeks of work ahead of M5. Yet M5's learning loop, which maps retention curves onto shots, is the part that needs real viewers, and the router mostly optimises providers you barely use (Vast is paused; RunPod and Kaggle are untested). I'd do it in this order: publish by hand now → a minimal M5 (publishing kit plus a retention fetch from the Analytics API) → M3-lite (just log RoutingDecision rows for the fallbacks you already have, which rule 6 requires anyway, a matter of days) → formats one at a time, each gated on channel data. That's a reasonable alternative to the current plan, and the call is yours.

On operations, C: had about 37 GB free on 18 Nov, down from about 56 GB two days earlier. All three Docker volumes live in the WSL disk image on C: ([ssd-move.md](../docs/runbooks/ssd-move.md)). No backup script exists. A corrupted WSL disk or a lost laptop would wipe every decision, cost record and asset. This is the cheapest serious risk to remove.

To make approvals easier on you, batch them: one demo a week covering several items, plus one always-current STATUS page in place of the stale hand-off. Track two numbers per video, ₹ per video and your minutes per video. The second tells you whether the 10-minute Review window is realistic.

## Conclusion

Project V has crossed the point where more engineering adds less value than one real upload. Every later system you've designed (taste learning, retention mapping, the bandit, the router) gets better with audience data and stays guesswork without it. So the most valuable "feature" this week is a published video and a backup. Seen that way, the hardening items aren't a detour from the roadmap. They're what lets you raise the Gemini cap and the publishing pace with confidence that a bug or a stranger on your Wi-Fi can't spend the budget twice.

Some things remain uncertain. The cost figures are list-price arithmetic, not ledger actuals, so a month of real CostEntry data will tighten them. YouTube publishes no official upload count that counts as "mass-produced". Test pass rates weren't re-run for this review. The one judgment I'd hold firmly is about order: quality and variation first, a slow and steady pace second, and the router and new formats after your first viewers tell you what works.
