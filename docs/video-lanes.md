# Video lanes: diagrams and the GPU broker (both OFF by default)

Two optional lanes for the long-form Video path. With nothing set, behaviour is exactly as before.

## A. Diagram lane (animated, labelled explainer shots)

For shots that explain how something works (parts, flows, layers, colour mixing, a signal chain). AI video hallucinates
mechanisms; a diagram is drawn from a scene spec, so it is accurate by construction and never a panned still.

**Switch:** `VIDEO_DIAGRAM_LANE=true` (`projectv.video.diagram-lane`, default `false`). Backend restart needed (read at startup).
Python worker: `uv sync` (adds Manim) and restart `uv run python -m workers.main`.

**How it flows**
1. Shot plan: the Director may set `lane: "diagram"` per shot (only when the switch is on and the video is 16:9; absent = `ai_video`).
2. For each diagram shot, `DiagramSpecs` asks the studio text model for a scene spec from the narration line (one call), validates it,
   makes **one** repair call showing the errors, and otherwise stores a text-card spec (title + the line, animated). The spec is stored
   in the shot's `prompt_json.diagramSpec`.
3. Keyframes skip diagram shots (they have none). Draft gen adds them to the clip batch as jobs with `mode: "diagram"`
   (1920x1080, 24 fps, shot length clamped to 5-12 s); the Python `generate_clips` activity draws them on the laptop with Manim
   (free, no GPU, no provider call) and the rest of the batch goes to the normal provider. The estimate counts them, so a batch of
   only diagrams still runs.
4. A drawn clip is a draft AND final quality: no final render is requested for it. Asset provenance: provider `diagram_manim`,
   licence own-render. To redraw one, redo that shot (it is picked explicitly).

**Renderer choice:** Manim Community (`workers/diagram/render.py`). It installs from wheels on Windows (pycairo, manimpango, skia-pathops)
and renders plain `Text` through Pango, so **no LaTeX** is needed; vector output is crisp at 1080p and about 0.5 MB per 10 s.
Each render runs in a subprocess (Manim keeps global state; a crash never takes the Temporal worker down); about 15 s per 10 s shot on this laptop.
Font: `DIAGRAM_FONT` (default Segoe UI; Pango falls back to its default sans if missing).

**Scene spec** (`workers/diagram/spec.py`, validated again in Python before drawing): elements `box layer circle line arrow beam text particles
dots_grid rgb_mix callout`; steps `show hide draw move color scale pulse flow mix` on a timeline (`at`, `dur`, `ease`). Palette is fixed
(`bg #0B1020`, cyan/blue/violet/orange/red/green/yellow, pure red/green/blue) so every diagram matches.

**Ready scenes** (`workers/diagram/templates.py`; rendered to `reports/tv-explainer/diagram-samples/`): CRT electron gun + shadow mask,
LCD layer stack with polarisers + backlight, RGB colour mixing, broadcast signal chain. Re-render:
`uv run python -m workers.diagram.templates reports/tv-explainer/diagram-samples`.

**Limits**
- 16:9 only (a vertical video ignores the lane). Diagram shots are 5-12 s.
- Shot-plan stage only: turning the switch on does not change existing shots; re-run Shot plan.
- The Upscale stage will still pass drawn clips through SeedVR2 (a 1080p vector render does not need it; skip those shots there if you want).
- The model draws the spec from one narration line; layouts can need a redo or an edit of `prompt_json.diagramSpec` (no dashboard editor yet).
- Quality of LLM-written specs is **not yet judged by eye** (see "Unverified").

## B. AI clips on the GPU broker (RunPod / Vast pod) instead of Modal / Omni

**Switches** (all read at startup; backend restart):

| Env | Property | Values | Meaning |
|---|---|---|---|
| `VIDEO_PROVIDER_DRAFT` | `projectv.video.provider-draft` | `wan` (default) / `omni` / `broker` | draft clips |
| `VIDEO_PROVIDER_FINAL` | `projectv.video.provider-final` | `omni` (default) / `wan` / `broker` | final clips |
| `VIDEO_PROVIDER_KEYFRAMES` | `projectv.video.provider-keyframes` | `default` / `broker` | FLUX.2 klein pictures on the pod |
| `VIDEO_GPU_IDLE_MINUTES` | `projectv.video.gpu-idle-minutes` | 5 | a failed batch lets go of the pod after this idle time |
| `DREAM_GPU_PROVIDER` | `projectv.dream.gpu-provider` | must be `broker` | the broker itself (shared with Dream) |

If a `broker` value is set while `DREAM_GPU_PROVIDER` is not `broker`, the batch is refused with that message.

**Behaviour**
- Same Wan 2.2 + FLUX pod worker, the same warm image (`projectv.gpu.warm-image`), the same single shared pod and Auto pick (cheapest live price)
  as Dream; `VideoGpu` uses `DreamGpu` (no copy). The Video id is the lease key; Dream and a Video can share the pod.
- The estimate shown before approval is pod seconds (config `seconds-per-81-frames`, start-up only if no pod is open) x the cheapest live price.
  The rental is requested with that expected duration, so the **daily GPU cap** and wallet checks apply; a refusal fails the stage into
  the normal "waits for the owner" state (Retry / Continue without it).
- Jobs are the same as the Modal route (480p drafts, 720p finals, hero takes, 4n+1 frames at 16 fps), rendered in turn on the pod.
- The pod is let go right after the batch is saved; a batch that failed leaves it to a 1-minute sweeper (idle >= 5 min, by lease heartbeat).
  Dream's "nobody is waiting" release will not end a pod a Video batch holds.
- **Cost:** the whole rental is booked as a CostEntry **on the Video's ledger** (`video_id`) when it ends, split by use if Dream shared it.
  No per-clip Modal rows are written for pod clips. A RoutingDecision (`gpu_pod`, capability `video.i2v.dream`) is logged for each batch.
- Finals on the broker follow the existing rule (drafts the owner did not mark "keep the draft"); Omni-only "hero finals" logic is not used.

## 2K delivery (2560x1440, 16:9, 24 fps)

All off by default (1080p behaviour unchanged). For the television video set, in `.env`:
`VIDEO_OUTPUT_HEIGHT=1440`, `VIDEO_DIAGRAM_WIDTH=2560`, `VIDEO_DIAGRAM_HEIGHT=1440` (backend restart; worker restart picks up nothing new).

- **Diagrams** render natively at the configured size (vector, so crisp; ~0.6-0.8 MB per 11 s at 2560x1440). The four samples in
  `reports/tv-explainer/diagram-samples/` are 2560x1440 (`... templates OUT_DIR [W H]`, default 2560x1440; pass `1920 1080` for the old size).
- **AI clips on the pod, 16:9:** drafts and finals are requested at **1280x720** (the pod worker accepts multiples of 16 up to 1280x720 pixels; `MAX_PIXELS` in
  `workers/pod/server.py`). Vertical 9:16 Videos keep 480x832 drafts, and Dream Shorts is untouched. 720p costs more pod time than 480p: the estimate scales
  the configured seconds per 81 frames by pixels^1.75 (Modal H100 measured 30 s vs 133 s; **not measured on a pod**, so check the first estimate against the real rental).
- **Assembly** takes mixed sources: each shot is scaled to fill with Lanczos and cropped to the master size. A clip that has to be enlarged (720p AI, 1080p Omni)
  also gets light luma sharpening (`unsharp=5:5:0.5:5:5:0.0`); a native-size diagram is left untouched. Master is x264 CRF 16, 24 fps.
- **Existing upscaler:** SeedVR2 7B on Modal (Upscale/"sharp" stage, `workers/modal_apps/upscale.py`). It outputs a **1080 px short side**, so it cannot make a 1440p
  master by itself, and Modal GPU work is capped at 0 until 1 Dec 2026. Diagram shots are skipped by it (they are already native). For this video use the
  Lanczos + sharpen path above (skip the Upscale stage, or "Continue without it").
- **Next quality step: a SeedVR2-class AI upscale of the AI clips to 1440p** (target height above 1080 in the SeedVR2 app, which needs a code change and a test).
  Cost from the studio's own estimate constants (`SharpenStage`: ~60 s of H100 per clip + 30 s per group of 8, ~64 s per 5 s clip) at the config's Modal H100 rate
  ($3.95/h, verify on modal.com/pricing): about **$0.85 per finished minute of AI footage at 1080p**; 1440p output will cost more (not measured). Only AI-video shots
  count (diagrams need none), so a 3-minute video with ~1 minute of AI clips is roughly $1. On a rented pod instead of Modal the same model would be billed at the pod's hourly price.

## Voice (ElevenLabs): not changed here
The provider rule lives in `workers/activities/voice.py` (`ELEVENLABS_COMMERCIAL`); there is no per-video field. To use ElevenLabs set
`ELEVENLABS_COMMERCIAL=1` in the Python worker's environment and restart the worker (no backend restart).

## Enable both lanes (what to do)
1. `.env` (owner / CTO; never printed): `VIDEO_DIAGRAM_LANE=true`, `DREAM_GPU_PROVIDER=broker`, `VIDEO_PROVIDER_DRAFT=broker`,
   optionally `VIDEO_PROVIDER_FINAL=broker`, `VIDEO_PROVIDER_KEYFRAMES=broker`. (Gemini/Omni stays the default for any kind left alone.)
2. `uv sync` (installs Manim).
3. Restart the control plane (`scripts\start.ps1`) and the Python worker (`uv run python -m workers.main`).
4. Create/continue the video; run **Shot plan** (diagram shots appear), keyframes, then Draft gen: approve the estimate (pod cost + "plus N diagram shots, free").

## Unverified
- Never run against a real pod or a real LLM: the broker path is tested with fakes (mocked `VideoGpu`/`DreamGpu`, real Postgres); the spec
  writer with a mocked `Llm`. No paid call was made.
- The pod worker was not exercised with 16:9 sizes (1344x768 pictures, 832x480 / 1280x720 clips); they are within its pixel limits by inspection.
- Diagram quality from LLM-written specs, and Manim on the production worker machine other than this laptop.
