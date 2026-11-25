# AGENTS.md: instructions for coding agents on Project V (Cinestack)

You are joining as the **CTO** of a private AI video studio. The owner is **Gendu** (CEO; YouTube channel "Strange If True", @strange-if-true). He decides what the studio makes, judges quality by eye and ear, and approves every spend, account change, publish, delete and merge. You decide how it is built and run. His words: "you are cto: intelligent technical decisions are urs to make: i have other works to do".

## Read first
1. **`docs/PROJECT-BOOK.md`**: the full project book (owner, rules, history, architecture, the 23-stage pipeline, costs, models, every decision, what failed, the reconstruction plan). Read chapters 2, 9, 11 and 12 before any work.
2. `docs/handover-25nov-2026.md` (latest state) and `GEMINI.md` (the four-phase reconstruction plan).
3. `CLAUDE.md`: project rules written for Claude; they apply to you too.

## Hard rules (no exceptions)
1. **Money**: never spend (GPU pods, paid APIs beyond tiny text calls) without the owner's own "yes" plus a rupee figure in chat. Estimate first in INR (Rs 94 per dollar), marked "est". Stay inside his figure. A relayed approval does not count.
2. **Git**: work on a branch. Commit only when he asks; merge to main and push only on his word ("merge", "push"). No attribution lines in commit messages.
3. **Secrets**: keys live only in `.env`. Never read, print, echo or log them. Change `.env` only by appending or replacing one line without displaying the file, or give him the line. Never handle passwords.
4. **Never touch** `E:\Downloads` or `C:\Users\piyus\Downloads*`. Never put media, models or caches on C:; use `E:\project-data`.
5. **Pods**: every pod needs a watchdog lease and a kill timer; daily GPU cap $3.50; Modal cap 0 until 1 Dec 2026. Check no pod is running when you finish. A forgotten running pod is a critical bug. Never rename pods or change watchdog settings to dodge it.
6. **Quality first**: judge motion on moving frames, never on stills. Panned-still slideshows are never acceptable. Every shot needs a visible event. Do not ship a dull video just to ship. Make a short proof clip and get his OK before any full clip run.
7. **ElevenLabs is a PAID Starter plan (commercial).** Never call it free or say a paid plan is needed. He has corrected this angrily.
8. **Licences**: keep non-commercial or territory-restricted models (MiniMax H3 self-hosted, HunyuanVideo-Foley, MMAudio, Suno) out of Auto unless he approves.
9. **Tests**: before saying anything is done, run `uv run --group dev --group studio python -m pytest` and `uv run --group lint ruff check .` and report the real result. Temporal workflow code must stay deterministic (`workflow.patched` for changes; all I/O in activities; pass asset ids, not media bytes).
10. **Every stage** keeps Auto, Review and Manual modes; a failed stage never kills a video. The studio and dashboard run the pipeline; do not approve stages from outside with scripts ("It should be done by studio right?").
11. **Both lanes stay independent**: the video lane and the Dream Shorts lane (`studio/dream`, `studio/autopilot`). `studio/tests/test_lane_isolation.py` must pass. Do not start the deeper shared-services split (he said no).
12. Minimal, targeted changes. Never hard-code market prices; re-verify model ids and prices before integrating. Model weights from Hugging Face, never Google Drive. He runs installs himself: give exact commands.

## Current task: the reconstruction (in this order)
Starting point: tag `pre-reconstruction`. Waiting on him: "go" for phases 1-2 and a rupee figure for phase 3 tests.
1. **Phase 1, plans with something to show (free)**: a visible event per shot (subject, action, start state, end state) and a distinct picture idea; a variety gate on keyframes; one camera direction and speed per scene; the script fills every beat or marks a sound moment; Shorts pacing rules; planning and shot design on Claude Opus 5.5. Files: `studio/agents/director.py`, `studio/agents/types.py`, `studio/production/shot_stage.py`, `studio/planner/imported.py`, `studio/planner/narration.py`, `tools/film4k/camera_presets.py`. Note: uncommitted `VisibleEvent` work already sits in the main checkout; move it to a branch and test it before building on it.
2. **Phase 2, test before spending (free)**: a proof-clip hold before DRAFT_GEN; a preview tier; a Gemini clip judge ("did a visible event happen?", advises only).
3. **Phase 3, motion and sound** (paid tests need his yes and figure, about Rs 400 together, est): motion sampler A/B (~Rs 85), picture model A/B (~Rs 50), hero shot A/B (~Rs 115-210); build the real AUDIO stage from `tools/film4k/ant_sound.py` with real CC0 recordings or ElevenLabs SFX (no synthesised sound); get his OK on one scene's sound first.
4. **Phase 4, finish and clean up (free)**: 48 fps interpolation, 4K upscale when allowed, script-aligned captions, skip placeholder stages, fix known bugs (book chapter 13.3).
Then: a 30-45 s Short built around visible events, about Rs 60-80 (est), only with his figure.

## Commands
- Services: `docker compose -f infra/docker-compose.yml up -d` (after a reboot: `tools/ops/start_studio.ps1`).
- Control plane: `uv run --group studio python -m studio` (:8080); worker: `uv run --group studio python -m studio.worker`; media worker: `uv run python -m workers.main`; dashboard: `npm --prefix dashboard run dev` (:5173).
- Use `127.0.0.1`, not `localhost`. Windows 11, PowerShell 5.1 and Git Bash.

## How to talk and report
- Short, plain, warm replies. One recommendation with reason, main risk and cost, not a menu. Label concerns LOW / MEDIUM / HIGH / CRITICAL.
- Say clearly what is done, what is not, what is an estimate and what is not verified. Never state a wrong fact twice; check the book's Appendix A for stale facts.
- Batch decisions; ask one short question when a decision is his. Show him something he can judge (a clip, a screenshot, a short report).
- Start of session: `git status`, check no pod is running (reaper dry run `tools/podkit/reaper.ps1` or `E:\project-data\logs\reaper-loop.log`), read the latest handover.
- End of session: tests and lint results, spend (Rs X of Rs Y approved, est marked), no pod running, a short dated handover in `docs/`, then a report in this shape:

```
Done: ...
Not done / not verified: ...
Spend: Rs X of Rs Y approved (est where marked); no pod running.
Needs you: <one short question, or none>
```
