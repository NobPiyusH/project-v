# Session hand-off — 16 Nov 2026 (read after CLAUDE.md and docs/HANDOVER.md "Revision R1")

## Where we are
- **Done & pushed** (`main` on github.com/NobPiyusH/project-v, private):
  - `d1755d6` M1 backbone (Temporal workflow, Auto/Manual, dashboard)
  - `8a9c516` M2a story pipeline (research → script → Lyria music → Gemini TTS + transcript check → beat-snapped shot plan → keyframes Nano Banana/Z-Image → animatic), Redo, **Review mode** (semi-auto timer)
  - `6052eeb` per-shot keyframe redo (notes, Auto/Premium/Free, versions, edit prompt)
  - `fc07b42` script line editing + per-line voice redo (takes, needs-voice flag)
- `af8fbf5` `workers/modal_apps/wan.py` (Wan 2.2 I2V on Modal H100, modes fast|hybrid|standard, CRF-12 encoding) + `workers/bench/wan_benchmark.py`.
- **Work X** (code-quality pass, committed after `af8fbf5`):
  0. Narration timing: `NarrationTiming` places lines from real voice lengths (Shorts: shrink gaps, then up to 8% tempo, never cut words); `ShotTiming.fitAnchored` pins each line's first shot; animatic applies tempo and extends the last shot. Fixes the night-sky Short (last sentence cut, drift). **Not yet re-run on that video** (redo Shot plan ≈ ₹15–20, needs owner OK).
  1. `scripts/start.ps1` / `scripts/stop.ps1` (`-All` also stops Docker). Logs in `logs/`, PIDs in `.run/`.
  2. `ProductionService` split into a facade + `ProductionDeps`, `StageSupport`, one class per stage, `ProductionReadModel`.
  3. `ProductionPersistenceTest`: real Postgres via Testcontainers (skips without Docker).
  4. Wan worker: no CPU offload on H100 (offload fallback on OOM), `generate_one` per clip via `.map` (up to 4 containers), live progress in `modal.Dict` `projectv-progress`. Redeployed; **speed not yet measured**.
  5. Model fallback lists (`ModelFallback.java`, `common.with_models`): retired/hanging/overloaded → next model; billing/quota errors don't hop. Verified live 16 Nov: 3.6-flash timed out → 3.5-flash → rule-based plan (Gemini itself was returning 504 for every text model at that moment).
- Tests: 35 Java (`./gradlew :control-plane:test`), 6 Python (`uv run pytest workers/tests`). All green.

## M2b (real video) — current state
- Wan 2.2 I2V A14B + lightx2v 4-step LoRAs (Apache-2.0) deployed as Modal app `projectv-wan`; weights cached in volume `projectv-hf-cache` (~160 GB incl. Z-Image).
- Benchmark 1 (star video `8fb917af-…`, shots 1/14/16/41, 720p 81 frames @16fps): **fast ≈ 115 s/clip, standard (30 steps) ≈ 1141 s/clip**, model load 77 s, total ≈ $1.9 Modal credit.
- Owner verdict: **"needs better quality."** Diagnosis: fast mode softens/mushes detail late in clip; encoding was 1.5 Mbps (fixed → CRF 12); 16 fps stutter (fix in finishing: RIFE → 24 fps, SeedVR2-class 4K upscale).
- Benchmark 2 RESULT: fast(HQ) ≈ 120 s/clip, **hybrid ≈ 483 s/clip (~4× fast, ~2.4× cheaper than standard)**; total ≈ $1.4. Reel was sent to the owner — get their verdict on hybrid vs fast vs standard.
- (Benchmark 2 setup, for re-runs:) shots 1 & 14 in `fast` (HQ encode) and new `hybrid` (full model + CFG on high-noise expert, LoRA only on low-noise, 12 steps). Output dir was the scratchpad (`…/scratchpad/wanbench2`) — likely lost; re-run:
  `uv run python -m workers.bench.wan_benchmark 8fb917af-8033-4581-9ec0-042652cbfed0 <outDir> 1,14 fast,hybrid`
  then show the owner the reel side by side with the earlier standard clip.
- Projected cost (fast, 1 take/shot): 4-min ≈ $7, 10-min ≈ $16 on H100. Planned speedups: no CPU offload on H100, parallel containers, per-clip jobs.

## Next steps (agreed)
1. Pick quality mode with owner (hybrid vs standard-for-heroes vs Flow/Veo manual for heroes).
2. Build the video stage: per-shot clip jobs (each clip saved as it finishes, live progress + cost in dashboard, stop button), drafts→finals, basic critic (black/frozen/flicker/length), take picker, per-shot clip redo (reuse shot-redo design).
3. M2c finishing: interpolation 24 fps, 4K upscale, colour match, beat cuts, SFX, clock overlay, 4K master + OTIO→FCP7 XML.
4. Later: style profile (learn from edits/notes), scientist researcher (OpenAlex/NASA ADS/arXiv), router with RunPod 4090 (fp8 small-GPU mode) / Vast, Blender/shader procedural shots, optional Claude Opus for scripts.

## Environment facts (don't rediscover)
- Repo `C:\dev\project-v`; JDK 21 at `C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot`; set `GRADLE_OPTS=-Djava.net.preferIPv4Stack=true`.
- uv/ffmpeg are under `%LOCALAPPDATA%\Microsoft\WinGet\Packages\…` (old shells lack PATH).
- Postgres on **5433**, Temporal 7233 (UI 8233), SeaweedFS S3 8333, API 8080, dashboard 5173 (Vite proxy → 127.0.0.1:8080).
- Gemini key in `.env` (billing on, app cap ₹400/month; ~₹125 spent). Text model `gemini-3.6-flash` (3.7/3.8 hang; 2.5 gone). Media models need API `v1beta`. TTS limit 10 req/min.
- Modal linked (workspace of owner); apps `projectv-zimage`, `projectv-wan`.
- Owner prefs: serious premium private studio; ask, don't assume; explain in plain terms; build efficiently; commit+push only when asked.
