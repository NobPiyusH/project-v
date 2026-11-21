# Handover, 21 Nov 2026 (evening): CTO chat to the next Claude

Read `CLAUDE.md`, `docs/HANDOVER.md` (from "Revision R1"), then this file, then `docs/handoffs/2026-11-20-account-handover.md` (still true for keys, caps, how to work with Gendu). No secrets here; values live in `.env` and `.secrets\`.

## Roles (owner decision, 21 Nov)
Gendu = CEO: decides what the studio makes and why, judges quality by eye and ear, approves every spend and irreversible step. Claude = CTO: recommends ONE way for each decision (reason, risk, cost), discusses, then starts after his OK; runs the work and reports failures plainly. Coding beyond a tiny edit goes to Opus low (Opus medium if hard); Sonnet only for routine work. Commit messages: no attribution lines. Push only on his "push"; he says "merge" himself.

## START HERE
1. `list-pods` on RunPod (MCP) and the dashboard GPU card. Today's rule: a pod must never be left running. The pod watchdog is now LIVE (it terminates studio-named pods with no lease after the grace period).
2. `git status` and `git log --oneline -8`. `main` is pushed to GitHub up to `f5363d2`; `39793ea` (studio safety) is merged locally and NOT pushed.
3. Check running sessions/Shorts: `select step,status,count(*) from dream_short group by 1`. Do not restart the backend while a Short or a pod is running.
4. Ask Gendu what he wants next. Open items below.

## What is on main now (all tested with fakes; real-pod proof listed per item)
- Stuck-step fix: the worker replaces a pod deleted mid-step (`PodGone`, `DreamGpu.replace`).
- Chain modes: last-frame (preferred by Gendu); first-last bridge exists, OFF.
- **Unfiltered look**: his prompt verbatim, one non-empty line = one shot (max 8), 5 s per shot, no writer/Producer/checks.
- **GPU power Auto/Manual** per Short (Manual keeps the pod on until he turns it off, idle 5 to 30 min).
- **One shared pod** for all Shorts (cost split by seconds used; `max-pods` 1).
- **Batch scheduling** (`3bb88bf` etc.): pictures of all Shorts first, then clips (no Z-Image/Wan swapping); timeouts count from when the pod starts a job; Retry reuses saved pictures/clips; the pod ends when no Short waits for GPU work.
- **Producer 30 s rule** (`ebf36e3`): when the AI Producer needs the owner's approval it waits 30 s, then decides alone (approves, or redoes if allowed). Modes: Auto = no AI check at all; Review = owner window only; Manual; Producer = AI reviews, redoes weak shots within rounds and budget, otherwise asks. Default for new Dream Shorts is REVIEW.
- **FLUX.2 klein 4B** on the pod worker (Apache-2.0, ~16 GB weights, first picture ~49 s then ~2.7 s). Blind A/B (11 prompts) vs Z-Image vs Nano Banana: Gendu said FLUX.2 is good, so it is the default (migration V40, config property; Z-Image still selectable). Results in `reports/picture-ab/`.
- **SVI 2.0 Pro scenes** (continuous long shots / 1-minute Shorts, `docs/svi-scenes.md`, `workers/dream/scenes.py`). Gendu: good, better than average; editing, sync, motion and continuity can be better.
- **Studio safety** (`39793ea`): SVI beats saved one by one with resume, never-ready pods replaced, watchdog live, **$3.50 per UTC day GPU cap** (`config/providers.yaml`, shown on the GPU card).

## Open items (in order)
1. **Backend restart pending.** The running backend does not yet have `39793ea` (restarted ~14:05 UTC on the batch+Producer code). Restart ONLY when RunPod's list is empty. Then tell session `studio-svi-safety` (id `local_86e5d8be-f36e-43e7-b477-55e895dadee9`, branch `claude/studio-svi-safety-52e8a5`) to run its paid test (hard limit $0.30: one SVI Short, 4 clips, FLUX pictures, 4090; stop the media worker once mid-chain and Retry to prove resume).
2. **90 s sailing Short retry** is running in session `local_8c124021-b7a7-43de-9053-a852e6447bdc` (cap $1.50, hand-written pod script on pod `kxf64ngwwkzcl8`, 5090). Two earlier hand-script runs lost everything (45 min self-stop mid-chain; pods never ready). A 60 s sailing Short with music exists: `reports/sailing-short/sailing-short-music.mp4` (+720p copy). Lesson: do not run long SVI chains from ad-hoc scripts; use the studio path.
3. **Money so far today:** RunPod posted about $2.96 for 13 pods before the 90 s retries; prepaid balance was $9.94. Disk cost is negligible ($0.136 of $9.32 over 30 days).
4. Next quality round (CTO plan): editing and music sync first (FFmpeg on the laptop, no GPU), then motion and continuity with small paid A/Bs.
5. Movie / short-story pipeline: session `movie-pipeline` (`local_e8d69dae-ba41-4347-aa83-e9609f554671`, branch `claude/movie-pipeline-273267`) waits for Gendu's "start" and a choice of how to keep a character consistent. Start it only after items 1 and 2.
6. Not merged on purpose: `claude/salad-route-81e327`. Known small risk: a clip file left from an old picture could be reused on Retry if a picture is redone before the retry (take number does not change); a bump of `clip_takes` broke existing tests, so it was dropped.
7. Refactor ideas (low priority): split `DreamService`, shared audio mixer, record builders, split the dashboard page, remove dead features.

## Two Claudes on one GitHub repo (rules we use)
Yes, it works if they never edit the same checkout or branch at once. 1) Each Claude works on its own branch in its own git worktree; only the CTO chat merges into `main`. 2) Before merging: `git fetch`, merge `main` into the branch, run the tests, then merge. 3) One writer per file area at a time; say in chat which files you are touching. 4) Never force-push, never rewrite `main`; push only on the owner's "push". 5) Do not restart the backend or touch pods while another session has a Short or a pod running; check `list-pods` first. 6) Do not stash with bare `git stash` (shared stash stack). 7) Migrations: take the next free `V__` number after fetching (today V40 is the last); two branches adding the same number conflict.
