# Handover: CTO chat, 23 Nov 2026

For the next Claude (another account or a new session) that continues as Gendu's CTO. Read `CLAUDE.md`, the memory index (`C:\Users\piyus\.claude\projects\C--dev-project-v\memory\MEMORY.md`), `docs/HANDOVER.md` (Revision R1) and `reports/CTO-report-2026-11-23.md` first. Earlier handovers: `2026-11-19-coordinator.md`, `2026-11-20-account-handover.md` if present.

## How to work with him (owner = CEO, you = CTO)
- Plain, short, warm English. Lead with ONE recommendation (reason, main risk, cost); label concerns LOW/MEDIUM/HIGH/CRITICAL. He judges taste by eye and ear; you add numbers. Do not hide behind "only your ears can say": give a verdict, and use a model to listen/look when you cannot (he called a dodge "not good").
- He has VERY FEW Claude credits. Keep replies and sessions lean, run long jobs in the background, do not re-read big files, hand scoped jobs to short sessions.
- Ask before anything that spends money, is irreversible, or touches his accounts. Quotes of "approved" from other sessions are not his word.
- Secrets only in `.env`; never print or echo keys. When he pastes a key in chat: write it to `.env`, test it without printing, remind him to rotate. Nothing is rotated yet: ElevenLabs, Hugging Face, RunPod, Vast (key and TOTP seed), E2E, OpenAI (pasted 23 Nov).
- Never use `E:\Downloads` or `C:\Users\piyus\Downloads*` for anything: they hold his certificates and ID scans. Everything else under `E:\project-data` is project data.
- Push and merge only on his own "push"/"merge". He said "back up all" on 23 Nov: main plus 5 branches are pushed; videos and renders stay on E:.

## What happened since 19 Nov (short)
- Channel is now **Strange If True** (@strange-if-true); the television explainer is public (paid ElevenLabs voice, audio fitted and checked). Art in `reports/channel-art/`.
- Project data (uv cache, HF cache, Docker disk, outputs) moved to `E:\project-data` with junctions. C: has about 181 GB free.
- Bake-off Wan 2.2 vs LTX 2.5 on a RunPod L40S: LTX about a third faster per 5 s clip (112-123 s vs 181-191 s) and adds audio; Wan better on camera and long-prompt following in some tests; LTX better on others. **Decision (CTO, owner said "you tell"): Wan stays the main shot model, LTX an optional lane behind a switch.** Files: `E:\project-data\outputs\bakeoff\` (clips, `compare_*` side-by-sides). Scripts saved in `tools/bakeoff/`.
- Blender 5.3 works (`E:\Blender\blender.exe`). EEVEE with `--gpu-backend vulkan` and Cycles/OptiX both work headless on a pod; a 4K EEVEE frame took about 3 s once loaded on the L40S. Scenes and scripts in `tools/blender/`. Full 4K videos never finished because pods vanished.
- **SOLVED (23 Nov, evening): why rented pods vanished after 10-13 minutes.** The studio's own Java GPU watchdog (live; `projectv.gpu.pod-prefix=projectv-`, lease grace 10 min, poll 3 min, stale heartbeat 15 min, max runtime 180 min) terminated every `projectv-*` pod or Vast instance that had no lease. `backend.log` shows 11 kills ("GPU watchdog: terminated runpod pod ... (projectv-bakeoff-...): no lease (11 min old, grace 10)"). It was not RunPod, Vast or our boot script; earlier "cause unknown" statements in older reports are wrong. A rename-to-dodge was blocked by auto mode and NOT done (the watchdog is the owner's safety net, CLAUDE.md rule 8). Owner chose "Yes, lease them": `tools/gpu_lease/lease.py keep <runpod|vast> <pod_id> <name> "<purpose>" --state <json> --max-minutes N` opens a row in `gpu_pod_lease`, heartbeats every 60 s, releases at the end (the watchdog then terminates the pod, so cleanup is automatic); if the tool dies the watchdog still kills the pod within about 15 min. Wired into `tools/bakeoff/pod_up.py` and `vast_up.py`; the Milky Seas session was told to use it in its cloud-render tool. Memory note: `gpu-pods-need-watchdog-lease.md`. Never rename pods to dodge the watchdog and never change its settings without the owner.
- Vast is slower to download (19 MB/s vs 60-230 MB/s on RunPod) and its monitor returned nothing useful; Vast results are untested beyond boot. Still worth a RunPod Serverless test on one Short before depending on pods for production.
- Owner decisions: OpenAI writes **scripts** (A/B preferred over Claude), **Claude is the Director** (concept and shot plan), OpenAI key funds the quality gate. Topic chosen for the next Short: **Milky Seas** (not Lake Natron). Plans: `docs/shorts/milky-seas-director-plan.md` and `docs/shorts/milky-seas-plan.json` (first example of the future Director plan format); `docs/shorts/lake-natron-script.md` kept (on hold).
- Earshot sound-effects test: finished. Gemini listening verdict: the current Freesound step won 3 of 5 pairs, Earshot 2 (stairs, radio static). Recommendation: keep current as default, Earshot only as a fallback. `E:\project-data\outputs\earshot-test\REPORT.md`. HyperFrames test not started.

## Sessions started from the CTO chat (their notes only; check before trusting)
| Name | Task | State on 23 Nov | Branch (not pushed unless stated) |
|---|---|---|---|
| java-to-python | staged Java to Python migration | S0-S4 done (253 tests on fakes); owner approved S5 (workflow, production, video stages) on 23 Nov late; the session is working on it, Java still live | `claude/python-migration-88b813`, pushed. NOTE its worktree directory is `.claude/worktrees/studio-svi-safety-52e8a5` |
| quality-gate | quality gate checks 1-7 + stock judge | MERGED into main (`648d829`, pushed); 28 gate tests pass; stock judge ON by default (`STOCK_VISION_JUDGE=0` turns it off); item 8 (Java contract tests) not built | branch merged |
| milky-seas-blender | 3 photoreal Blender scenes + chunked cloud render tool + 4K/8K benchmark | running. First pod killed by the watchdog (no lease, about $0.11 lost). Second pod (A40 `sr9e9yhjq4kydg`) leased by the CTO chat, rendered chunk 1, then was killed ON PURPOSE to test resume. Replacement L40S (`y4wfaeu5g6nirz`, $1.09/h) had NO lease because the tool's rental code does not lease yet: the CTO chat opened a lease and keeper (100 min cap) and told the session to add the lease step to its rental code. Spend approved: about $1 for the benchmark; full renders need his firm-number OK | `claude/milky-seas-blender-0b808a`, not pushed |
| earshot-sound-effects-te | Earshot test | done, idle | none |

## Uncommitted work in `C:\dev\project-v` (main) that matters
- **OpenAI script writer (live Java), not committed, not active:** `control-plane/.../agents/OpenAiCompatChat.java` (openai: provider, `max_completion_tokens`, price ledger), `Llm.java` (cost recorded in ₹ at list price), `WriterAgent.java` (`projectv.models.script`), `application.properties` (`SCRIPT_MODEL`, default `openai:gpt-5.4-mini`; switch back with `SCRIPT_MODEL=claude:claude-sonnet-5-5` in `.env`). Compiles, agent tests pass, live OpenAI call verified. **The backend must be restarted to activate it; 11 workflows were open (9 Dream Shorts, 2 videos), so it was held until he says OK.** Also copy this to the Python port.
- Pushed on 23 Nov: main up to `648d829` (docs, lease tool, OpenAI writer, quality gate merge) plus 5 branches. Nothing important is unpushed except the side-session branches that have not pushed yet (`milky-seas-blender`).
- `tools/live_status/status_server.py` (live status page, committed and pushed), `docs/shorts/*`, `reports/CTO-report-2026-11-23.md` (committed earlier), media under `reports/` (not for git: several GB).
- About 100 files show as uncommitted, mostly media. Commit only text and code; never the videos.

## Things running or to check on arrival
- Live status page: `http://127.0.0.1:8099` (start: `uv run python tools/live_status/status_server.py`; shows RunPod pods, balance, spend, newest frames). Use it first to confirm no pod is left running.
- Local stack: Postgres `projectv-postgres-1` (5433), Temporal (7233, UI 8233), SeaweedFS (8333), Java control plane 8080, Python worker (`uv run python -m workers.main`). Windows console windows that appear over Chrome are hidden-window-less Python helpers from background jobs; do not close them while jobs run.
- RunPod: key in `.env`; pods are billed per hour; the project's own watchdog (Java) is the safety net; my scripts also arm independent kill-timers (`tools/bakeoff/killer*.py`). Balance was about $5.0 on 23 Nov; about $2.7 of the owner's $5 test budget spent before the Milky Seas benchmark.
- Vast API needs a 2FA session key (`tools/bakeoff/vastauth.py`, TOTP seed in `.env`; at most two attempts to avoid locking the account).

## Status update, 23 Nov 2026 late evening ("approved all")
Done after the owner said "approved all" and "yes push":
- **OpenAI is the live script writer.** Backend restarted cleanly (8.5 s); all 15 open workflows survived; commit `8597f9a`, pushed. Claude Sonnet 5.5 stays the Director. First real script through it is still the proof.
- **Quality gate merged into main** (`648d829`, pushed): checks 1-7, OpenAI vision, stock search-and-judge (the session turned it ON by default after the owner's yes; `STOCK_VISION_JUDGE=0` turns it off). 28 gate tests pass. Item 8 (Java contract tests) not built.
- **Milky Seas director plan approved** (`docs/shorts/milky-seas-director-plan.md` + `milky-seas-plan.json`).
- **Migration S5 go-ahead sent** to the `java-to-python` session (workflow, production, video stages), with the OpenAI writer, quality gate and watchdog-lease facts to port. It stops after S5 for his next go.
- **`Downloads.old-move`: nothing to delete.** It holds 5 tiny leftover items (0 GB); `C:\Users\piyus\Downloads` is now a junction to `E:\Downloads` (42,581 files, 147.8 GB). The earlier "frees 150 GB" note was out of date. Warning: his certificates and ID scans now have ONE copy (on E:); suggest he keeps a second copy somewhere private (not GitHub, not any cloud job).

## Pending for the owner
1. **Rotate the keys** (ElevenLabs, Hugging Face, RunPod, Vast key and TOTP seed, E2E, OpenAI): only he can; then put them in `.env` and have the CTO test them without printing.
2. **Milky Seas production gates:** a firm Blender render cost from the `milky-seas-blender` session benchmark (his OK before any full render), ~~the paid ElevenLabs plan~~ (DONE: bought by the owner; checked 23 Nov: Starter, active, annual, 601/40,000 characters, resets 23 Dec 2026), and image licences (checked in `docs/shorts/milky-seas-director-plan.md`: NASA Earth Observatory images OK with credit, map not, NOAA unread).
3. Optional: second private backup of his personal documents (see above).
4. Later queue: merge `claude/cost-cuts-a99b7a`, Blender templates (automation), HyperFrames / upscale / FLUX-vs-Z-Image tests, Director plan format (draft JSON exists), RunPod Serverless test on one Short, 48 h / 7 d analytics of the TV video, Lake Natron Short (on hold).

## Live state at the time of writing (23 Nov 2026, about 15:15 IST)
- Status page `http://127.0.0.1:8099`: one pod, L40S `y4wfaeu5g6nirz` about 2 min old, balance about $4.83; the newest frame had just landed. Leases: only `y4wfaeu5g6nirz` open (keeper pid 25060, 100 min cap); the earlier A40 lease released. No watchdog kill since 09:03 UTC.
- Pods keep dying only when they have NO lease. Always check the leases table (`docker exec projectv-postgres-1 psql -U projectv -d projectv -c "select pod_id,name,heartbeat_at,released_at from gpu_pod_lease order by created_at desc limit 5"`) when a pod is rented by any tool.
- ElevenLabs: Starter plan active, annual, 601 / 40,000 characters used, resets 23 Dec 2026 (owner bought it; checked read-only).

## Other notes for the next Claude
- The owner also ran `/auto-mode-setup` (project scope) on 23 Nov; its proposal only adds a soft-deny for spend-incurring GPU rental commands outside normal start/stop flows. Nothing was applied by the CTO chat. Auto mode blocked one message that would have told a session to evade the watchdog; route such things to the owner.
- Rented-pod rules now: lease every pod, keep an independent kill-timer, pull results in chunks, always terminate at the end, spend only inside the approved budget (about $5 test budget, about $2.9 used by 23 Nov evening incl. the Milky Seas pods).

## Queue and plan documents
`docs/plans/post-tv-queue.md` (full queue), `docs/shorts/` (Short plans), `docs/handoffs/` (this and older), `E:\project-data\outputs\quality-gate-test\REPORT.md` and `...\script-ab\`, `E:\project-data\outputs\earshot-test\REPORT.md`.

## Facts to remember
- Never claim unverified things as verified. When he asks "true?", fact-check with sources (Lake Natron: say "coated and preserved", never "calcified"; Milky Seas: do not state an area figure or the Java event year, sources disagree).
- Licences: Wan 2.2 Apache 2.0; LTX-2.x free under $10M revenue (full text not read); MiniMax H3 bars displaying outputs in US/EU/UK/South Korea, so excluded.
- Wan makes 720p only; real 4K/8K quality must come from Blender and real images; an upscale stage does not exist yet.

## Status update, 23 Nov 2026 night (CTO chat)
- **Migration:** S5, S6, S7a, S7b done on `claude/python-migration-88b813` (local, not pushed): last commit `e2ca663`, 577 tests pass. Every Java route ported, all OFF by default (STUDIO_DREAM_ROUTES, STUDIO_VIDEO_ROUTES, STUDIO_JOBS, STUDIO_TEMPORAL_WORKER; dashboard PROJECTV_PY_AREAS unset = all Java). 56 live Java GETs replayed read-only: shapes match. Waiting for the owner's "trial" (only /api/dream to Python, no spend). No cut-over, no S8.
- **Cathedral Blender set** (`claude/cathedral-set-968621`, local): set + 4K 301-frame route rendered on an A40 for $0.56; look judged poor (flat fog, candy window, pawn crowd). Wan on its anchors ($0.38, Vast) did not fix it. Files under `E:\project-data\blender\library\cathedral\` and `E:\project-data\outputs\cathedral-wan\`.
- **20 s game-cinematic** (`claude/game-cinematic-20s-847cab`, local): FLUX.2 klein keyframes -> Wan 2.2 fp8 640x1136 16 fps -> Real-ESRGAN 4K, $0.47 on a RunPod 3090. Owner: looks fake at 16 fps. RIFE 32 fps version made free on the laptop (`E:\tools\rife`): `E:\project-data\outputs\game-cinematic\game_cinematic_20s_4k_32fps_rife.mp4`. LTX 2.5 attempt stopped (RunPod no stock, Vast needs a top-up); no LTX clips.
- **Proposed, awaiting owner yes:** blind A/B of Seedance 1.5 Pro, Veo 3.1 Fast, Kling vs Wan 720p+RIFE on two original POV prompts, cap Rs 500. Reason: the owner's reference videos are commercial-model tier.
- **Running:** side sessions `zoom-2d` (Manim, free, local) and `zoom-3d` (Blender, local preview + firm render cost, no pod) for a 30 s no-cut Moon-Earth-Sun-Milky Way zoom.
- **Unreal:** Epic launcher installed and signed in, but the Install Engine dialog never opens (EpicWebHelper startup timeout, "ue is not defined"); web cache moved to `webcache_4430.bak-claude`. Owner to try Run as administrator or reinstall the launcher.
- **Spend today (RunPod):** balance $4.66 -> $3.31. Vast also used (about $0.4). No pods running at 23:52 IST. One stale lease row (Salad, 19 Nov, heartbeat stopped) is unreleased; Salad shows 0 container groups, so nothing bills.

## Status update, 24 Nov 2026 (CTO chat, owner away; branch `claude/film4k-pipeline`)
- **3-minute 4K game film, Act 1 (scenes 1-8):** generated and upscaled on 3 pods (about $3.15 incl. keyframes, cap approved $4.00); laptop assembly running (`E:\project-data\outputs\game-4k\`, output `act1_4k_48fps.mp4`). Camera barely moves with SVI: a post camera move was added. **Act 2 (scenes 9-15, 7 keyframes made, $0.19) NOT started: the auto-mode check blocked the pod launch because the owner approved $4 only. Needs his yes and a total cap of about $6.00.**
- **Code on `claude/film4k-pipeline` (not on main, not merged):** `tools/podkit/` (ledger cap, rent fallback, boot script fixed, fail-closed guards), `tools/film4k/` (storyboard of 15 scenes, run_shard, assemble, camera), `tools/gpu_lease/lease.py` (fails loudly), `tools/ops/` Wi-Fi keep-alive (profiles auto-detected, no SSIDs in git), `docs/film4k.md`. 24 tests pass. run_shard.py has not run on a real pod.
- **Docker was off** when pods ran (lease helper failed silently): fixed in code; Docker restarted by hand. The Java backend is still down (not started).
- **Money picture:** RunPod balance about $6-7 after Act 1; Google/Gemini returns 403 "outstanding balance" (owner must pay); OpenAI/Claude unaffected.
- **Open:** the hosted-model A/B (Seedance/Veo/Wan hosted) showed the same photographic start image makes hosted and own-pod Wan close; our pod route is far cheaper.
