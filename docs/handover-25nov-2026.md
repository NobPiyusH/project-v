# Project V (Cinestack) handover notes, 25 Nov 2026

Written by the CTO session for the owner (Gendu, CEO; channel Strange If True). Read `docs/HANDOVER.md` and `CLAUDE.md` first; this file is the latest status on top of them. Prices at Rs 94 per dollar; estimates are marked.

## 1. What the project is
A personal AI video studio: text prompt to a full 4K video (script, narration, music, sound, visuals, edit, captions, publishing kit) plus Shorts. Python only: `studio/` (FastAPI, Temporal Python SDK), `workers/`, `tools/`. GPU work on rented RunPod pods (ComfyUI, Wan 2.2 14B fp8 + LightX2V 4-step). Data on `E:\project-data` (outputs, models, caches); never touch `E:\Downloads` or `C:\Users\piyus\Downloads*`.

## 2. Owner rules (do not break)
- Spend only with his own "yes" and a figure in the CTO chat (he said on 25 Nov that his yes there covers side sessions; quote his words in briefs). New figure, new account, key handling: ask.
- Merge and push to main only on his word ("merge", "push"). No attribution lines in commits.
- Keys only in `.env`, never printed. Nothing rotated yet (ElevenLabs, HF, RunPod, Vast, E2E, OpenAI, AICredits, others): owner job.
- Quality first: judge motion before long renders; panned-still slideshows are never acceptable.
- Every pod: watchdog lease (`tools/gpu_lease/lease.py`) plus independent kill-timer (`tools/bakeoff/killer.py`); ledger cap `tools/podkit/ledger.py`; $3.50/day GPU cap. Modal cap 0 until 1 Dec 2026.
- Side coding sessions: Sonnet or Opus, effort at most medium. Short plain replies. Technical decisions are the CTO's.

## 3. What is built and on main (origin/main at 95a4cd1)
- **Python-only studio**: Java control plane removed (tag `java-final`; restore with `git checkout java-final -- control-plane`). Migration runner `studio/migrate.py`. See `docs/java-removal.md`.
- **Film pipeline**: `tools/film4k/` shot-level editor `edit.py`, procedural sound `sound.py`; Act 1 of the 3-minute game film done (`act1_cut_v3`).
- **Camera control**: `camera_presets.py` (presets, varied per-scene plan), `camera_graphs.py` (Fun Camera, VACE, repair graphs), `repair.py` (mask, composite, splice; reroll vs inpaint), `tools/podkit/groups.py` (model download groups, about 40 GB for Fun Camera). Docs: `docs/film4k.md`, `docs/pod-tests-24nov.md`.
- **Quality gate** `tools/qualitygate/`: tier 1 (loudness, black, frozen frames, captions), tier 2 (motion, independent motion, sharpness, flicker, colour drift), shot-cut detection (`--shots auto`), tier 3 OpenAI vision judge (OFF by default). Hooked after the edit stage (`workers/activities/gate_check.py`, film CLI). A fail holds the stage (Retry / Continue without it); warns only log. Docs: `docs/quality-gate.md`.
- **Pod reaper** `tools/podkit/reaper.py` + `reaper.ps1`: kills pods over wall-clock (default 3 h, `maxh=N` suffix), `projectv-*` pods with no lease after 15 min, or past the daily cap. Dry-run unless `--apply`. Docs: `docs/pod-reaper.md`. NOT scheduled yet.
- **Vetting lab** `tools/vetting/`: read-only scanner for third-party tools (licence, install hooks, binaries, secrets+network, unsafe loads). `docs/vetting-lab.md`.
- **Quality tooling**: ruff config, pytest testpaths, PR-only CI, `tools/metrics/code_metrics.py`, old-vs-new analytics in `docs/analytics/`.
- **Research**: `reports/AI video studio landscape.md` (adoption plan; the Adopt now / Test next / Ignore / Watch lists).

## 4. Test and spend results (24-25 Nov)
- Pod tests: Fun Camera fixes the static camera (zoom in and pan left judged good by eye; roll presets warp). About $0.60 of a $1 cap.
- Camera scene test: 3 beats at 720x1280, 81 frames, 16 fps; boot about 10 min, about 3.6 min per beat, about $0.32 (estimate; bill not posted). Dragon consistent; blue drift in beat 3 and road blur. Judged from stills only; owner has not watched the clip: `E:\project-data\outputs\camera-scene\camera_scene_720p.mp4`.
- Film Act 1: $3.15 of $4 cap. Act 2 declined at a $6 cap; needs a new yes and figure.
- Vision judge trials: about Rs 4.4 of the Rs 50 cap. The small judge misses the blue wing and falsely calls good beats near-static: do not trust it as a defect detector; free checks catch panned stills and near-static scenes.
- Current cut `act1_cut_v3` measures -21.1 LUFS (too quiet vs -14 target): the sound step should normalise it.
- Pods now: none running (checked 25 Nov). RunPod balance was $4.73.

## 5. Decisions and findings
- Wan 2.2 stays the main model. MiniMax H3: weights public but licence excludes EU, UK, South Korea, US (use, hosting, displaying outputs); viewer case unclear; open weights 768p only, 33B: ignore for the channel.
- REA (morluto/rea, reverse-engineering tool, not a video tool): LOW to MEDIUM value; not installed. If wanted, download `rea-agents` from npm (about 186 MB) into its own empty folder and scan with the vetting lab first. Needs the owner's yes.
- Adobe: not needed. Do not share another person's Adobe login. DaVinci Resolve Free opens the exported FCP7 XML.
- Black-box study of premium apps only: no cracking, no extraction.

## 6. Open items
Priority order (CTO recommendation):
1. Run the full test suite on main after the last merge (pushed before the run finished; result pending when this was written).
2. Fix quiet audio: normalise loudness in the sound step. Free.
3. Blue-wing repair with VACE (about $0.5 pod run; needs his yes and figure).
4. Real Short through the Python path on live Temporal: never done (risk HIGH). Needs a spend figure. `run_shard.py` never run on a pod.
5. Schedule the pod reaper (Task Scheduler every 10 min); first real pod needs a dry-run to confirm API field names (`desiredStatus`, `costPerHr`, `lastStartedAt`).
6. Character and location bank plus per-beat timing in the plan; script-aligned captions (WhisperX alignment); publishing set-up (Private first, AI-disclosure flag, `publishAt`).
7. Audio: switch fallback to Kokoro and ElevenLabs, add ElevenLabs Music/SFX while Lyria is blocked (uses his paid plan: ask). Owner paused voice tests on 18 Nov.
8. Tests from the research report (each needs his yes and a figure): SeedVR2 vs Real-ESRGAN (a SeedVR2 test ran on 24 Nov; no verdict recorded), interpolation order and GIMM-VFI (about $1-2), LongCat-Video and LTX-2.5 (about $3-8 each), hosted hero-shot tier (about $10-15), narration blind A/B (under Rs 100).
9. Pick Short A or B (`docs/shorts/two-shorts-plans-24nov.md`) and a cap.
10. Clean up: 15 stuck old Java workflows in Temporal; JDK removal only with his ok; Windows ADB/Wi-Fi leftovers are owner jobs.

## 7. Owner jobs
Rotate the pasted keys; pay the Google billing (403); update the Wi-Fi driver; Unreal decision (install failed, parked); watch the camera-scene clip and give a verdict; decide on Act 2.

## 8. Commands
- Services: `docker compose -f infra/docker-compose.yml up -d`; after a reboot `tools/ops/start_studio.ps1`.
- Control plane: `uv run --group studio python -m studio` (:8080); worker: `uv run --group studio python -m studio.worker`.
- Tests: `uv run --group dev --group studio python -m pytest` (use the project defaults: a bare path such as `tools/` collects a Blender script that fails). Lint: `uv run --group lint ruff check .`.
- Quality gate: `uv run python -m tools.qualitygate <video> --shots auto --judge off`.
- Pod reaper dry run: see `docs/pod-reaper.md`.

## 9. Sessions and memory
Side sessions are archived once merged (restorable). Memory notes live in `C:\Users\piyus\.claude\projects\C--dev-project-v\memory\` (index `MEMORY.md`). Worth re-reading: `cto-mode`, `owner-working-rules`, `spend-approval-via-cto`, `pod-tests-24nov`, `gpu-pods-need-watchdog-lease`.
