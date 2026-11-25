# How one video goes through the studio, end to end

Written 25 Nov 2026 for the owner (CEO). Everything here was read from the code, `config/providers.yaml`, the live database (read-only) and the studio API (read-only); nothing was run that spends money. Where I could not verify something it says **not verified**. Section 7 lists them all.

Money: the studio's ledger books everything at **₹94 per $** (`pricing.fxInrPerUsd` in `config/providers.yaml`), except OpenAI calls and the OpenAI quality-gate judge, which are booked at **₹90 per $** (`Settings.inr_per_usd`, `config/quality-gate.yaml`). I verified the 94 on live rows: a RunPod rental of 0.3053 h at $0.69/h is booked as ₹19.80, and a Claude call of 3,047 tokens in / 1,059 out is booked as ₹1.57. My own conversions of estimates use ₹94 per $ and are marked "estimate".

---

## 1. One-page overview

There are **23 stages** in the code (`studio/workflow/stage.py`), in this order. The plan decides which ones a video needs; the others are recorded as "Skipped".

```
 YOU (dashboard / API)  --create video--->  PLAN
                                              |
   Legend:  [L] your laptop (free)            v
            [A] paid cloud API call      RESEARCH [A] -> CONCEPT [A] -> SCRIPT [A] -> MUSIC [A] -> NARRATION [A*]
            [P] rented GPU pod (paid)                                                                  |
            [M] Modal GPU (capped at 0)                                                                v
            [F] free service                 SHOT_PLAN [A] -> KEYFRAMES [P + A review] -> ANIMATIC [L] -> LIBRARY_MATCH [F]
            [-] placeholder, does nothing                                                              |
            *   ElevenLabs paid Starter plan (commercial)                                              v
                (prepaid credits, ledger shows ₹0)     DRAFT_GEN [P] -> FINAL_GEN [P or A] -> CRITIC [-] -> TAKE_SELECTION [-]
                                                                                                       |
                                                                                                       v
                                             UPSCALE [M] -> AUDIO [-] -> ASSEMBLY [L + free API] -> MOON_LAYER [L, only if plan has a moon block]
                                                                                                       |
                                                                                                       v
                                             INTERPOLATE [L, off by default] -> ROUGH_CUT_REVIEW [-] -> PACKAGING [-] -> FINAL_APPROVAL [-] -> PUBLISH [-]
```

How the run is controlled:

- **Every stage** can run in **Auto**, **Review** (waits a window, default 10 minutes, then continues by itself), **Manual** (waits for you) or **Producer** (an AI producer reviews and advises; in this build it only advises, `adviseOnly: true`). You can switch a stage mid-run. Producer is a fourth mode; the project notes usually say "three modes".
- **Before a paid stage runs** it can wait for your Approve / Skip ("hold before run"): always for a stage in Manual mode, and for every paid stage of a video that has `approve_paid_first` on (on by default for plan files).
- **After a stage runs**, the Inspector (free checks) looks at the result. A fail is redone once automatically; a second fail always waits for you.
- **A failed stage never kills the video.** It waits for you: Retry (Reject), Continue without it (Approve), or Resume (re-run only the save when the media work is already done).

Who does the work:

| Where | What runs there |
|---|---|
| Your laptop (free) | The control plane (API, Postgres, Temporal, SeaweedFS in Docker), the "media worker" (voice orchestration, ffmpeg edit, Inspector, quality gate, Moon composite, optional RIFE interpolation) |
| Rented GPU pod (RunPod, paid by the hour) | Pictures (FLUX.2 klein 4B) and video clips (Wan 2.2 14B fp8 with the Fun Camera graph, ComfyUI) |
| Cloud APIs (paid per use) | Claude (plan, research, concept, shot plan), OpenAI gpt-5.4-mini (script), Gemini (music, picture and clip reviews, some text), ElevenLabs (voice), Groq Whisper (free speech check) |
| Modal (capped at ₹0 until 1 Dec 2026) | UPSCALE only (RIFE smoothing on an L4, SeedVR2 sharpening on an H100) |

---

## 2. Stage by stage

Costs are from the **ledger rows** of the three videos in section 4 unless another source is named. "Plan file" means a video started from a finished plan (`POST /api/videos` with `planFile`, e.g. `docs/shorts/moon-10x-closer-plan.json`): for those videos PLAN, CONCEPT, SCRIPT and SHOT_PLAN are built from the file and call no paid model (`studio/planner/imported.py`).

"Hold before run" applies only to the stages in the `HOLDABLE` set: SHOT_PLAN, MUSIC, NARRATION, KEYFRAMES, DRAFT_GEN, FINAL_GEN, CRITIC, UPSCALE, TAKE_SELECTION, AUDIO, PACKAGING (`studio/workflow/video_production.py`). The Inspector covers RESEARCH, SCRIPT, MUSIC, NARRATION, SHOT_PLAN, KEYFRAMES, ANIMATIC, DRAFT_GEN, FINAL_GEN, ASSEMBLY.

| # | Stage | What it does (plain words) | Who does it, exact id, where | Paid? Cost driver | Typical cost and source | Mode / gate | You see / approve | What can fail, what the studio does |
|---|---|---|---|---|---|---|---|---|
| 1 | **PLAN** | Turns your prompt into a video plan: format, length, aspect ratio, style preset, whether it needs research, narration, music. Decides which stages run. | Claude `claude:claude-sonnet-5-5` (`CLAUDE_STORY_MODEL`), cloud API. Falls back to Gemini, then to a rule-based plan with no model. Plan file: no model. | Yes: tokens. Plan file: free. | ₹1.57 (ant, 4,106 tokens, ledger). ₹1.58 (TV video ledger row). Plan file: ₹0 (Moon). | Not holdable. Normal mode rules apply. | The plan summary line (title, format, seconds, preset). Approve, or Reject with notes to re-plan. | No key or model failure: rule-based plan (it can misread, e.g. an old bug ignored "15-second"). Never blocks. |
| 2 | **RESEARCH** | Finds 8-20 facts with web sources; then a free check whether each number is on its cited page. | Claude `claude:claude-sonnet-5-5` with Anthropic web search (max 5 searches, one attempt only). If it fails: Gemini with Google Search (`gemini-3.6-flash` chain). Source check: free, on the laptop. | Yes: tokens + web searches ($10 per 1,000 searches). | ₹8.11 (ant, 2 searches, ledger). ₹8.23 (TV). Skipped when the plan says no research (Moon, ant plan). | Inspected (NO_FACTS fails). Not holdable. | Fact count and how many were confirmed on their page. | No Claude: Gemini. Few facts or none: Inspector warns or fails. Unconfirmed numbers are marked so the writer avoids them. |
| 3 | **CONCEPT** | Big idea, acts, optional on-screen "clock". | Claude `claude:claude-sonnet-5-5`, cloud. Plan file: built from the file. | Yes: tokens. | ₹1.61 (ant). ₹1.90 (TV). Plan file ₹0. | Not inspected, not holdable. | Logline and big idea. | Model fails: falls through the text chain (Gemini, NVIDIA, Groq, OpenRouter free tiers). |
| 4 | **SCRIPT** | Writes the narration lines (and a voice name, a reading style, a music brief). Checks length against the plan. | OpenAI `openai:gpt-5.4-mini` (`SCRIPT_MODEL`; owner A/B 23 Nov), cloud. Plan file: your lines word for word. | Yes: tokens. | ₹0.46 (ant, flagged estimate). Earlier TV run on Claude: ₹3.83. Plan file ₹0. | Inspected (too many words for the length = FAIL). | Line count, word count, voice, warnings. | Too long: Inspector fail, one automatic redo with the findings as notes, then it waits for you. (Moon attempt 1 failed this way, attempt 2 passed.) |
| 5 | **MUSIC** | One instrumental score, extended with 4 s crossfades to the video length, then beat-mapped. Skipped when the plan says no music. | Google Lyria `lyria-3.5` (fallback `lyria-3-pro-preview`; `GEMINI_MUSIC_MODEL`), cloud; beat map with librosa on the laptop. | Yes: per song ($0.08 list x 1.5 calibration). Up to `ceil(seconds/55)+1` songs, max 6. | ₹11.28 per song (Moon ledger, 1 song, flagged estimate). | Holdable. Inspected. **Lyria provider cap: ₹15 per video, ₹30 per day** (see section 5: the cap check looks at the maximum number of songs, not the actual). | Score length, BPM, beat count. | Google billing returned 403 on 24 Nov (docs); worked on 25 Nov (Moon). If it fails the stage waits: Approve = continue without music. |
| 6 | **NARRATION** ("Voice") | Voices each script line as its own WAV, listens back to each one (Whisper) and re-voices bad takes. | Provider choice in `workers/activities/voice.py`: Chatterbox (laptop, only if installed and `PROJECTV_CHATTERBOX` is not 0) else Gemini TTS unless `ELEVENLABS_COMMERCIAL=1`, in which case **ElevenLabs `eleven_v4_turbo`, voice George** when credits cover it. Fallbacks: Gemini TTS `gemini-3.8-flash-tts` (voice Charon by default), then Kokoro on the laptop. Check: Groq Whisper `whisper-large-v3` (free). | ElevenLabs paid Starter plan (owner's, commercial): prepaid credits, so the ledger shows ₹0. Gemini TTS: tokens. | ₹0.00 (Moon: 30 credits; ant: 156 credits for 9 lines). Gemini TTS alternative: config estimate ₹15 (`holdEstimates.NARRATION`, estimate). | Holdable. Inspected (speech must match the script). | Seconds of speech, how many lines were re-voiced. You can edit a line or re-voice one line. | 2 takes per line on ElevenLabs, 3 on Gemini. Failure on ElevenLabs: whole video re-voiced with Gemini. The script says "voice Charon" but ElevenLabs speaks as George: the script's voice name only applies to Gemini. |
| 7 | **SHOT_PLAN** | Breaks the video into shots timed to the narration and (when there is music) the beat; writes an image prompt and a motion prompt per shot, picks the camera move, marks hero shots (max 20%). | Claude `claude:claude-sonnet-5-5` as "Director", cloud. A second paid text review by the "Creative Director" (see note). Plan file: one shot per plan beat, free; the review is skipped for plan files. | Yes: tokens (output-heavy). | ₹12.30 Director + ₹7.02 review = ₹19.32 (ant, 45 shots, ledger). TV (32 shots): ₹8.18 to ₹8.52 per Director call. Plan file ₹0. | Holdable. Inspected. | Shot count, scenes, hero count, timing warnings (e.g. "narration is 61 s, too long for a 60 s Short"). | Voice too long for a Short: warning and fail. Dead air, shots under 0.8 s: warnings. Redo once automatically on FAIL. |
| 8 | **KEYFRAMES** | One still picture per shot, before any video money is spent. | With `VIDEO_PROVIDER_KEYFRAMES=broker` (live on 25 Nov per the ledger): **FLUX.2 klein 4B on a rented RunPod pod**. Default (not broker): Gemini `gemini-nano-banana-2.1` for hero shots, Z-Image Turbo on Modal for the rest. Then a picture review by Gemini `gemini-3.6-flash` (`GEMINI_VISION_MODEL`, cloud) judging meaning, accuracy, continuity, no writing. | Yes: pod time by the hour (rate read live, e.g. $0.69/h RTX A6000, $0.89/h RTX 4090) + a few tokens for the review. | Moon: pods ₹12.42 (three rentals) + review ₹2.60 = ₹15.02 for 2 pictures, 3 attempts (ledger). Code estimate for 18 pictures: 900 s boot + 10 s per picture on the pod = about ₹20-25 at $0.69-0.89/h (estimate). | Holdable. Inspected, plus the Creative Director's story check. Hold text says "about ₹85" for 18 pictures: that uses the **Gemini image price**, not the pod price (see section 7). | The pictures, review scores, and a weak-shot list. Reject with notes redoes only the weak shots. | Moon attempt 1 crashed saving the result (a missing-default field in the result record; the fields now have defaults in `studio/media/types.py`; see section 4). Review fail: one automatic redo of only the failing shots (a paid redo waits for you unless `auto_redo_paid`). A bad pod: client replaces a lost pod once. |
| 9 | **ANIMATIC** | A rough preview cut of the pictures + voice + music so you can check story and pacing before video spend. | ffmpeg on the laptop. | Free. | ₹0. | Inspected. | A 1080p preview video. | Missing keyframes: stage fails and waits. |
| 10 | **LIBRARY_MATCH** | Looks for free real footage (NASA, Wikimedia Commons) for shots the Director marked. You pick; picked footage replaces that shot's generated clip. | Laptop, NASA and Wikimedia free APIs. | Free. | ₹0. | Not holdable. | Candidate clips per shot (only if any shot asked for real footage). | A miss never fails the shot. |
| 11 | **DRAFT_GEN** | Makes one moving video clip per shot from its picture. Camera presets: every clip of a 9:16 video on the pod route uses a real camera move; beat 2 starts from beat 1's last frame (chains of 2). | **Wan 2.2 14B fp8 Fun Camera** (`wan2.2_fun_camera_*_14B_fp8_scaled`, LightX2V 4-step LoRAs) in ComfyUI on the rented pod, 720x1280, 81 frames at 16 fps, one take per shot. Other routes (not live): Modal H100 Wan, Gemini Omni `gemini-omni-1.2-flash`, SVI. Diagram shots: Manim on the laptop (flag off by default). | Yes: pod hours (about 3.7 min per beat + about 6 min setup + the pod boot). | Moon: ₹19.80 for 2 beats, one pod, 0.3053 h (ledger). Studio's own estimate for 18 beats of the ant plan: **₹94 to ₹188, 87 min** (from `POST /api/plans/validate`; high figure = a second take of every clip). | Holdable; **always stops at its own estimate** unless `PROJECTV_VIDEO_AUTO_APPROVE_INR` is above it (default 0). Inspected (frozen picture, black frames, flicker, wrong size). | The estimate (clips, minutes, ₹, $) before anything runs; Approve or Reject (Reject skips the stage, nothing spent). | Never retried by Temporal (a retry would pay again). Failure waits for you. Moon: the clips were made and paid for, then the save crashed (a missing default field, fixed in code at 15:08 IST on 25 Nov). Clip stuck / pod lost: the broker's lease, watchdog and kill-timers end the pod (section 5). |
| 12 | **FINAL_GEN** | Full-quality second pass for shots whose draft was approved and not marked "keep the draft". | Wan hybrid on pod, or Gemini Omni 720p for hero shots only (`VIDEO_PROVIDER_FINAL`). | Yes, if there is anything to render. | ₹0 for camera clips: "No shots need final clips" (Moon). A camera clip is already final quality, so a final pass runs only when you pick a shot. | Holdable; asks its own estimate like DRAFT_GEN. Inspected. | Estimate, only if there is work. | Same as DRAFT_GEN. |
| 13 | **CRITIC** | Meant to be the automatic quality check of every take. | **Nothing yet**: `control.run_stage` placeholder. | Not built. Hold estimate in config says ₹5 although the stage spends nothing. | ₹0. | Holdable (the hold would ask about ₹5 it does not spend). | A line saying "Placeholder (real work arrives in M2+)". | Cannot fail. |
| 14 | **TAKE_SELECTION** | Meant to pick the best take per shot. | **Nothing yet** (placeholder). | Not built (estimate 0, never held). | ₹0. | Normal modes. | Placeholder line. | None. |
| 15 | **UPSCALE** | Smooths motion to a true 24 fps (RIFE, L4 GPU) and sharpens each clip to 1080 p short side (SeedVR2 7B, H100). | **Modal** apps `workers/modal_apps/finish.py` (Practical-RIFE) and `upscale.py` (`seedvr2_ema_7b_fp16`), not the rented pod. | Yes: Modal GPU seconds ($0.000222 /s L4, $0.001097 /s H100). | Moon estimate shown: smoothing about ₹2 + sharpening about ₹25 for 2 clips; code formula for 18 clips: about ₹3 + ₹130 (estimate). | Holdable; always asks. **`modal.monthlyCapUsd` is 0**, so it is refused: "₹0 of this month's GPU cap left". | An estimate and an Approve / Skip. | Declined or refused: stage is skipped and the edit uses the clips as they are (what happened on Moon). The target is **1080p, not 4K**. |
| 16 | **AUDIO** | Meant to be the final audio mix. | **Nothing yet** (placeholder). The real mixing happens inside ASSEMBLY. | Not built (estimate 0). | ₹0. | Normal modes. | Placeholder line. | None. |
| 17 | **ASSEMBLY** | The edit: every shot's chosen clip on the narration timeline (clips are slowed to fill their slot, up to 1.6x flagged), voice + ducked music + optional sound effects, mastered to -14 LUFS, word-timed captions burned in, SRT, and a Premiere package. Then the quality gate. | ffmpeg and OpenTimelineIO on the laptop; Whisper `whisper-large-v3` on Groq (free) for caption timing; Freesound (CC0/CC-BY only, needs `FREESOUND_API_KEY`); `tools/qualitygate` tiers 1-2 (judge off). | Free. | ₹0 (Moon). | Inspected; quality gate on (`QUALITY_GATE_ENABLED=1`). Default master **1080p** (`VIDEO_OUTPUT_HEIGHT=0`; 1440 gives 2560x1440). | Master summary, report of the gate (pass / warn / fail), the SRT and the Premiere zip. | A shot with no moving video stops the stage before rendering (never a panned still). Gate FAIL: stage waits (Reject = redo the edit, Approve = continue without the gate). Warnings only appear in the text. A crashed gate does not block. |
| 18 | **MOON_LAYER** | Optional. Composites a growing Moon picture over the master (scale curve, glow, caption). Only for a plan file with a `moon` block. | ffmpeg on the laptop (`tools/film4k/moon_layer.py`). The Moon picture is a local file you provide. | Free. | ₹0. | Redone together with ASSEMBLY / UPSCALE. | The composite; you judge the Moon by eye. | Missing picture or ffmpeg error: waits for you (Reject = retry, Approve = continue without). |
| 19 | **INTERPOLATE** | Optional. Doubles the master's frame rate (24 to 48 fps), audio untouched. | RIFE `rife-ncnn-vulkan`, model `rife-v4.6`, on the laptop's RTX 3050. Off unless `INTERPOLATE_FPS=local`. | Free. | ₹0. Measured about 7.5 s of work per second of video (docs). | Off by default, so skipped. | Result or "Interpolation is off". | RIFE missing or crash: clear skip or waits for you. |
| 20 | **ROUGH_CUT_REVIEW** | Meant to turn your notes into changes. | **Nothing yet** (placeholder). | n/a | ₹0. | Normal modes. | Placeholder line. | None. |
| 21 | **PACKAGING** | Meant to make captions, dubs, titles, thumbnails, Shorts. | **Nothing yet** (placeholder). Captions (SRT) are already made inside ASSEMBLY. | Not built (estimate 0). | ₹0. | Holdable in name; Moon had it Manual and it sat waiting. | Placeholder line. | None. |
| 22 | **FINAL_APPROVAL** | Meant to be your last check. | **Nothing yet** (placeholder). | n/a | ₹0. | Normal modes. | Placeholder line. | None. |
| 23 | **PUBLISH** | Meant to upload as Private to YouTube / Instagram. | **Nothing yet** in the video workflow (placeholder). A YouTube uploader exists (`workers/youtube/`) but only the Autopilot / Dream path calls it, not this video workflow. | n/a | ₹0. | Normal modes. | Placeholder line. | None. |

Notes on the table:

- **Creative Director.** Two jobs: (a) a text review of the shot plan (`CREATIVE_REVIEW`, a paid text call; skipped for plan-file videos), (b) a picture review after KEYFRAMES (Gemini vision, `GEMINI_VISION_MODEL`, default chain `gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite`). In code (a) passes no preferred model, so it follows the text model list (`GEMINI_MODEL`, then the fallbacks). On the ant video the ledger shows it booked to Claude Sonnet 5.5 (₹7.02): I could not reconcile that with the code (section 7).
- **AI Producer** (Producer mode): a multimodal Gemini model (`gemini-3.6-flash`, set in `ProducerService`) reads, looks and listens and recommends approve / redo / ask. Today it only advises. After your decisions it also writes "learn rules" notes through the text chain; on the Moon video those two calls landed on the free NVIDIA tier (₹0).
- **Mode names.** Auto passes by itself. Review waits a window (default `reviewMinutes` 10, per video) and continues. Manual waits for you. Producer = advice first.
- **Redo rules** (`REDO_WITH` in `studio/video/service.py`): redoing SCRIPT also redoes NARRATION, SHOT_PLAN, KEYFRAMES, ANIMATIC. NARRATION and MUSIC redo ANIMATIC. SHOT_PLAN redoes KEYFRAMES, ANIMATIC. KEYFRAMES redoes ANIMATIC. UPSCALE redoes ASSEMBLY, MOON_LAYER, INTERPOLATE. ASSEMBLY redoes MOON_LAYER, INTERPOLATE. Clip stages (DRAFT_GEN, FINAL_GEN) each run alone, after showing an estimate. RESEARCH and CONCEPT run alone. Which redos cost money: everything except ANIMATIC, ASSEMBLY, LIBRARY_MATCH, MOON_LAYER (`studio/production/redo_policy.py`); a paid automatic redo waits for you unless the video has `auto_redo_paid`.
- **AI Producer redo stop rule** (`redo_stop_rule.py`): if a redo review names the same problem twice (visible people, text, morphing, too little motion) it tries one firm prompt fix, then asks you instead of paying for another GPU round.

---

## 3. How the models are chosen and how to change them

All keys live only in `.env` (git-ignored). I did not open `.env`, so the **live values are not verified**; the table shows the code default and where to change it. Restart the control plane after changing a text or planner setting, and the media worker (`workers.main`) after changing a worker setting. The video pipeline reads these through `studio/api/video_wiring.py` and `studio/config.py`.

| What | Setting (env var) | Code default | Where it is read |
|---|---|---|---|
| Text model chain for plan fallbacks, research fallback, picture-check fallback | `GEMINI_MODEL` | `gemini-3.6-flash` | `studio/config.py` `text_model` |
| Backup text models, in order | `GEMINI_TEXT_FALLBACKS` | `gemini-3.5-flash`, NVIDIA nemotron, NVIDIA step-5, Groq gpt-oss-120b, OpenRouter ernie and gemma free, `gemini-3.5-flash-lite` | `studio/config.py` |
| Director and planner model (plan, concept, shot plan) | `CLAUDE_STORY_MODEL` (the name is **not** `STORY_MODEL`) | `claude:claude-sonnet-5-5` | `studio/config.py` `story_model` |
| Script writer | `SCRIPT_MODEL` | `openai:gpt-5.4-mini` (set `claude:claude-sonnet-5-5` to go back) | `studio/config.py` |
| Claude thinking effort | `CLAUDE_EFFORT_OVERRIDES` e.g. `SHOT_PLAN:low` | medium everywhere | `studio/agents/llm.py` |
| OpenAI list prices used for the ledger | `OPENAI_PRICE_IN`, `OPENAI_PRICE_OUT` | $0.75 and $4.50 per million tokens | `studio/config.py` |
| Gemini voice model and voice | `GEMINI_TTS_MODEL`, `GEMINI_TTS_VOICE` | `gemini-3.8-flash-tts,gemini-3.1-flash-tts-preview`; `Charon` | `video_wiring.py` |
| Gemini image model | `GEMINI_IMAGE_MODEL` | `gemini-nano-banana-2.1,gemini-3.1-flash-image` | `video_wiring.py` |
| Music model | `GEMINI_MUSIC_MODEL` | `lyria-3.5,lyria-3-pro-preview` | `video_wiring.py` |
| Picture-review (vision) model | `GEMINI_VISION_MODEL` | `gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite` | `video_wiring.py` |
| ElevenLabs | `ELEVENLABS_API_KEY`, `ELEVENLABS_MODEL`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_VOICE_NAME`, `ELEVENLABS_RESERVE_CHARS`, `ELEVENLABS_COMMERCIAL` | `eleven_v4_turbo`, George (`JBFqnCBsd6RMkjVDRZzb`), reserve 1,500 characters; auto-use needs `ELEVENLABS_COMMERCIAL=1` | `workers/activities/voice.py` (media worker) |
| Laptop voices | `PROJECTV_CHATTERBOX` (`.env.example` sets 0 = off), `KOKORO_VOICE` | Kokoro `bm_george` | `voice.py` |
| Clip provider per kind | `VIDEO_PROVIDER_DRAFT`, `VIDEO_PROVIDER_FINAL` | `wan` (Modal) and `omni` (Gemini); `broker` = the rented pod | `video_wiring.py`; the live 25 Nov runs used the pod |
| Pictures | `VIDEO_PROVIDER_KEYFRAMES` | `default` (Gemini + Z-Image on Modal); `broker` = FLUX.2 klein on the pod | `video_wiring.py` |
| Gemini Omni model id | `OMNI_MODEL` | `gemini-omni-1.2-flash` | `video_wiring.py` |
| Fun Camera moves | `VIDEO_CAMERA_PRESETS` (default true), `VIDEO_CAMERA_ALLOW_UNTESTED` (default false) | tested presets: `zoom_in`, `pan_left`, `pan_right`, `slow_push`, `drift_right` | `tools/film4k/camera_presets.py` |
| One pod for pictures and clips | `VIDEO_ONE_POD`, `VIDEO_ONE_POD_IDLE_MINUTES` | off (not proven live); 5 | `video_wiring.py` |
| Clip spend that does NOT ask | `PROJECTV_VIDEO_AUTO_APPROVE_INR` | 0 (always asks) | `video_wiring.py` |
| Master size | `VIDEO_OUTPUT_HEIGHT` | 0 = 1080p; 1440 = 2560x1440 (16:9) | `video_wiring.py` |
| Quality gate | `QUALITY_GATE_ENABLED` | on (tiers 1-2, judge off). Thresholds: `tools/qualitygate/config.py`; judge model `gpt-5.4-mini` and its caps: `config/quality-gate.yaml` | `video_wiring.py` |
| Moon layer / interpolation | `MOON_LAYER` (default true), `INTERPOLATE_FPS` (default off; `local` = on) | | `video_wiring.py` |
| Rented pods | `DREAM_GPU_PROVIDER` (`broker` to allow pods; default `modal` = none), `POD_CLOUD_PREFERENCE`, `POD_WARM_IMAGE`, `POD_VOLUME_ID`, `POD_IMAGE`, `GPU_MAX_PODS` (1), `GPU_MAX_HOURLY_USD` (2.0), `GPU_MAX_RUNTIME_MINUTES` (180), `GPU_IDLE_MINUTES` (15), `GPU_LEASE_GRACE_MINUTES` (10), `GPU_WATCHDOG_DRY_RUN` (false) | | `studio/config.py`, `studio/api/wiring.py` |
| Prices, caps, FX rate, estimate numbers | edit `config/providers.yaml` (never `.env`) | | `studio/ledger/pricing.py` |
| Which pod models are used | **Not an env setting**: fixed in `workers/pod/server.py` (Wan 2.2 fp8 experts, LightX2V LoRAs, FLUX.2 klein 4B `flux-2-klein-4b.safetensors` + `qwen_3_4b` + `flux2-vae`, Fun Camera files, SVI LoRAs). Changing a pod model is a code change. | | |
| Style presets (visual style text per type of video) | `config/style-presets.yaml`: `cinematic_awe`, `explainer_clean`, `documentary_story`, `ambient_visual`, `shorts_punchy` | | `studio/planner/presets.py` |
| Spoken-pace estimate | `config/narration.yaml`: 2.5 words/s English, warns at +10%, rejects the plan at +25% | | `studio/planner/narration.py` |

Observed in the running API (not from `.env`): `GET /api/system` answers `"planner":"gemini:openai:gpt-5.4-mini,gemini-3.6-flash"`, which means the live text model setting starts with OpenAI `gpt-5.4-mini`, then `gemini-3.6-flash`. That differs from the code default `gemini-3.6-flash`, so `GEMINI_MODEL` is set in the live environment (inferred, not verified).

---

## 4. Cost of a whole video

### 4.1 Real videos (live database and API, read-only)

Source: `GET /api/videos/<id>`, `GET /api/videos/<id>/cost`, and `select ... from cost_entry` (SELECT only), read on 25 Nov 2026 about 15:28 UTC (20:58 India time). Rate: ledger ₹94 per $ (₹90 for the OpenAI row).

**Read this first: two different "spent" numbers.** The video's `spent_inr` column (the figure on the video list) adds every ledger row **except GPU pod rentals**, because the broker writes rentals straight into `cost_entry` without adding them to that column (`PgBrokerRecords.cost` vs `CostLedger.record`). The budget guard, the per-video cap and the cost view add **all** ledger rows. So Moon's list screen says ₹13.88, the true ledger total is **₹46.10**. I use the ledger total.

#### A. First live Moon Short: "What if the Moon were 10 times closer?"  (id `c2200780-34dc-4f79-b705-b2f1c4b13f45`)

Plan file, 9:16, 2 beats of 5 s, Fun Camera `slow_push`, 14 s master (13.79 s). Status now: waiting at PACKAGING (a placeholder stage in Manual). Cap ₹150 (default).

| Stage | What was paid | ₹ | $ at ₹94 |
|---|---|---|---|
| PLAN, CONCEPT, SCRIPT, SHOT_PLAN | plan file, no model | 0.00 | 0.000 |
| MUSIC | Lyria `lyria-3.5`, 1 song (estimate flag) | 11.28 | 0.120 |
| NARRATION | ElevenLabs `eleven_v4_turbo`, 30 credits, paid Starter plan | 0.00 | 0.000 |
| KEYFRAMES, pod rentals | 3 RunPod rentals (attempt 1: 0.0006 h RTX 4090 $0.89/h = ₹0.05; attempt 2: 0.1103 h A6000 $0.69/h = ₹7.15; attempt 3: 0.0806 h = ₹5.22) | 12.42 | 0.132 |
| KEYFRAMES, picture review | Gemini `gemini-3.6-flash`, 2 reviews of 2 images (₹1.37 + ₹1.23) | 2.60 | 0.028 |
| DRAFT_GEN | 1 RunPod RTX A6000 rental, 0.3053 h = 18.3 min, 2 Fun Camera beats | 19.80 | 0.211 |
| UPSCALE | skipped (Modal cap ₹0; its estimate was about ₹2 + ₹25) | 0.00 | 0.000 |
| AUDIO, ASSEMBLY, MOON_LAYER | laptop | 0.00 | 0.000 |
| Producer "learn rules" (2 calls) | NVIDIA free tier | 0.00 | 0.000 |
| **Total (ledger)** | | **46.10** | **0.490** |

Of the total: pods ₹32.22 (70%), Gemini ₹13.88 (30%). Pod time 0.4968 h (about 30 min over 4 rentals). The finished second cost **₹3.34** (`costPerSecondInr`). It used about 25% of the $2 (about ₹188) you approved. The video list column shows ₹13.88.

#### B. First ant attempt: "What an ant hears at night"  (id `539db64a-ab73-4623-8af7-a7e35d68fba6`)

Prompt route (paid planner), all stages Manual, `approve_paid_first` on, cap ₹180. Status: waiting at SHOT_PLAN (it flagged the narration as 61 s, too long for a 60 s Short). **No pod or GPU spend.**

| Stage | What was paid | ₹ | $ at ₹94 |
|---|---|---|---|
| PLAN | Claude Sonnet 5.5, 3,047 in / 1,059 out | 1.57 | 0.017 |
| RESEARCH | Claude + 2 web searches, 27,543 tokens | 8.11 | 0.086 |
| CONCEPT | Claude, 3,432 in / 1,021 out | 1.61 | 0.017 |
| SCRIPT | OpenAI `gpt-5.4-mini`, 3,723 tokens (estimate flag; booked at ₹90/$) | 0.46 | 0.005 |
| NARRATION | ElevenLabs, 156 credits, 9 lines, paid Starter plan | 0.00 | 0.000 |
| SHOT_PLAN | Claude Director, 3,977 in / 12,290 out (45 shots) | 12.30 | 0.131 |
| SHOT_PLAN (Creative Director review) | booked on Claude, 10,981 in / 5,269 out | 7.02 | 0.075 |
| **Total (ledger)** | | **31.06** (the list column shows 31.07 from rounding) | **0.330** |

#### C. Ant redo: "What an ant hears at night"  (id `a013569d-50c7-44e6-9c75-159cc25ca005`) **IN PROGRESS**

Built from the plan file `docs/shorts/ant-hears-at-night-plan.json` (90 s, 18 beats, 9:16), `approve_paid_first` on, cap ₹148.90, PLAN to SCRIPT Manual (approved), the rest Auto. PLAN, CONCEPT, SCRIPT and SHOT_PLAN cost ₹0 (plan file). NARRATION ran on ElevenLabs (156 credits, ₹0). **It is now stopped at KEYFRAMES on the hold-before-run prompt**: "Waiting before it spends: 18 pictures and their AI review ... about ₹85. Budget of this video: ₹149 left of ₹149." So it is waiting for your Approve / Skip, not rendering. **Spent so far: ₹0.00 ($0.00)**, no pod rented (checked with `GET /api/gpu/pods`: none).

| Item | ₹ |
|---|---|
| Spent so far (ledger) | 0.00 |
| Waiting: KEYFRAMES (hold text says about ₹85; real pod estimate about ₹20-25 plus about ₹5 review, see 4.2) | not yet spent |
| Still to come if all approved: 18 Fun Camera clips | studio estimate ₹94-188 (87 min) |

#### Extra data point: the 180 s TV film  (id `d3e53cd1-520e-4dd3-8ecb-b6ed0714aa93`, completed, 2560x1440, 32 shots)

Ledger total ₹318.52 ($3.39): Vast pod ₹124.98, Gemini ₹83.78 (diagram specs, music, reviews), Claude ₹67.06, RunPod ₹29.54, OpenAI vision checks ₹12.53, ElevenLabs ₹0. The list column shows ₹140.63. That is ₹1.77 per finished second.

### 4.2 Estimate for a typical 90 s video of 18 five-second beats

Every number below is derived from the config formulas and the real rows above. It is an **estimate**, not a quote.

Assumptions:
1. 9:16 vertical, 18 beats of 5 s, 720x1280, 81 frames at 16 fps, Fun Camera chains of two, one take per beat.
2. Pictures and clips on the RunPod pod, rate between $0.69/h (RTX A6000) and $0.89/h (RTX 4090) as seen on 25 Nov; one boot for pictures, a second boot for clips (one-pod mode is off).
3. Narration on ElevenLabs's paid Starter plan (prepaid credits, so the ledger shows ₹0). If it were Gemini TTS use the config's ₹15.
4. No FINAL_GEN pass (camera clips are final quality). Draft clips: one take.
5. No video music unless noted (the ant plan has none).
6. No UPSCALE (Modal cap is 0 until 1 Dec 2026), no INTERPOLATE, laptop stages free.

| Item | Low ₹ | High ₹ | Basis |
|---|---|---|---|
| Text models (plan file) | 0 | 0 | plan file |
| Text models (from a prompt, 45 shots like the ant) | 30 | 35 | ant ledger ₹31.06 up to and including shot plan |
| Narration | 0 | 15 | ElevenLabs free / Gemini TTS estimate |
| Music, if wanted | 0 | 34 | Lyria ₹11.28 per song, up to 3 songs for 90 s (and see the cap problem in section 5) |
| Keyframes pod (18 pictures) | 10 | 25 | studio formula: 900 s boot + 10 s per picture = 1,080 s at $0.69-0.89/h |
| Picture review (Gemini) | 4 | 6 | 17 images cost ₹4.66 on the TV film |
| Clips pod (18 beats) | 94 | 188 | studio estimate from `/api/plans/validate`: 87 min; high = second take |
| Producer notes, Inspector, ASSEMBLY, gate | 0 | 1 | free tiers and laptop |
| **Plan-file video, no music** | **about 110** | **about 235** | about $1.2 to $2.5 |
| **Prompt-route video with 1 song** | **about 150** | **about 300** | about $1.6 to $3.2 |
| Add UPSCALE later (smooth + sharpen 18 clips on Modal) | +133 | +133 | formula in `finishing_stages.py` (about ₹3 + ₹130), only after the Modal cap is raised |

Checks against the limits: the default per-video cap is ₹150 (`pricing.perVideo.defaultCapInr`), so the **high end of a plan-file 90 s video would be refused by the cap** (the hold prompt prints "Over the cap" and says to raise the video's budget); the ant video was given ₹148.90. The daily GPU cap of $3.50 (about ₹329) is not the limit for one video. A mid estimate of the Moon rate (₹3.34 per finished second) would be ₹300 for 90 s, but the Moon had a fixed 5-minute pod boot cost spread over 14 s.

---

## 5. Gates and safety

**Per-video cap.** `pricing.perVideo.defaultCapInr: 150` (about $1.6); a video can have its own budget (`budgetInr` at creation; 0 = no cap). Before every paid call and pod rental the guard checks spent + reserved + estimate <= cap and refuses; the stage then waits for you (Retry / Continue without it). Spent includes failed attempts and aborted pods.

**Hold before run.** Described in section 1 and 2. A stage in Manual or a video with `approve_paid_first` stops with the estimate and the video's remaining budget **before** the first paid call; Reject skips the stage and spends nothing. Estimates: music, keyframes, clips and UPSCALE are computed live; SHOT_PLAN ₹3, NARRATION ₹15, CRITIC ₹5 come from `pricing.holdEstimates`; an estimate that cannot be made still holds ("unknown cost is not free"). DRAFT_GEN, FINAL_GEN and UPSCALE always show their own estimate and wait unless `PROJECTV_VIDEO_AUTO_APPROVE_INR` is above it.

**Daily and monthly caps (`config/providers.yaml`).**

| Cap | Value |
|---|---|
| All GPU pods per UTC day | $3.50 (`gpuBroker.dailyCapUsd`, set again on 25 Nov) |
| All rented GPUs per month | $20 total; RunPod $12, Vast $12, E2E $5, JarvisLabs $0, others $0 |
| Modal GPU (UPSCALE, old Wan route) | monthly cap **0** until 1 Dec 2026 |
| Gemini, all features | ₹1,000 a month (₹681 spent, ₹319 left on 25 Nov per `/api/budget`) |
| Claude | $8 a month (₹752; ₹200 spent) |
| AICredits text fallback wallet | ₹150, only if `AICREDITS_FALLBACK_ENABLED=true` (live setting not verified) |
| ElevenLabs Starter plan (paid, owner bought 23 Nov 2026) | 40,000 credits a month, commercial licence (confirm on the Terms page before the first publish) |
| Quality-gate OpenAI judge (off by default) | $0.50 per run, $2.00 per day |
| Pod limits (`Settings`) | 1 pod at a time, hourly price at most $2.00 (runbook sets 1.0), 180 min maximum run, 15 min idle |

**Provider caps (added 25 Nov at 17:19 IST, after the Moon run).** Per provider and per video, and the stage that may use it:

| Provider | Daily ₹ | Per video ₹ | Stages |
|---|---|---|---|
| `gemini_lyria` | 30 | 15 | MUSIC, DREAM_MUSIC |
| `gemini_image` | 60 | 40 | KEYFRAMES |
| `gemini_image_review` (reviews, judges, critics) | 40 | 20 | any |
| `gemini_omni` | 150 | 100 | DRAFT_GEN, FINAL_GEN |
| `gemini_api` (text) | 200 | none | any |
| `groq_whisper` | free, no limit, logged | | |

A call that would break one is refused and the stage waits for you. **A finding:** `MusicStage` asks the cap about the **maximum** number of songs (2 for a 10 s Short, 3 for 90 s), so it asks for ₹22.56 and ₹33.84; with the config as it is, I checked by a dry calculation (no network, nothing spent) that both would be refused (per-video ₹15 and daily ₹30 versus one real song at ₹11.28). The Moon run's music passed only because it ran before the provider caps existed. Raising `gemini_lyria` `perVideoInr` to at least ₹34 and `dailyInr` to at least ₹34 would let a 90 s video with music through.

**Pod safety (rule 8, "a forgotten running pod is a critical bug").** Layers, in order:
1. **Lease.** Every rental goes through `GpuBroker.acquire()`, which writes a lease row (`gpu_pod_lease`) and heartbeats it. The studio's watchdog (every 180 s, live, not dry-run) terminates any pod named `projectv-*` that has no lease after 10 min, a released lease, a stale heartbeat (idle 15 min), or has run past 180 min. It never touches pods without the prefix.
2. **Self-stop inside the pod** (`studio/gpu/self_stop.py`): the pod's own wrapper terminates itself at an absolute deadline, when idle (no heartbeat and GPU under 5% busy), or when its job ends, even with the laptop off.
3. **Kill-timer / reaper** (`tools/podkit/reaper.py`, `reaper.ps1`): independent of the studio; kills RunPod pods over 3 h (or the `maxh=` in the name), `projectv-*` pods with no lease for 15 min, or pods that would pass the day's $3.50. **Dry-run by default and not scheduled**: docs say not scheduled yet, and a Windows scheduled-task query found no task named like "PodReaper" (not conclusive).
4. **Release before any human wait.** In one-pod mode the workflow lets the pod go before a pause, a review window, a manual hold, a failure or a cancel; a 30 s job ends a held pod after 5 min.
5. **Hold timer, idle release (every minute), per-batch release** after each save.
6. **Boot checks** (`POD_CLOUD_PREFERENCE=community_then_secure`, never run on a real pod): abort a Community pod with no boot log within 9 min. Default is `secure`.
Observed now: `GET /api/gpu/pods` lists no pods; the watchdog's last sweep found 0 studio pods. Two **old lease rows are still unreleased** (`projectv-salad-...` from 19 Nov and `projectv-motionblock` from 24 Nov); no such pods exist, so this is only stale bookkeeping, but nobody has cleaned it up.

**Inspector, Creative Director, Producer.** Free second-level checks (Whisper, ffmpeg, picture statistics, structural rules) after the stages listed in section 2. A FAIL redoes the stage once; a paid redo waits for you unless `auto_redo_paid`; a second FAIL waits for you whatever the mode.

**Quality gate** (`tools/qualitygate`, runs after ASSEMBLY, free). Tier 1: loudness (target -14 LUFS, tolerance 1.5, true peak below -1 dBTP), black frames, frozen frames, caption timing. Tier 2: motion, independent motion (catches a panned still: below 1.5 fails, below 4.5 warns), sharpness, flicker, colour drift. Tier 3, the OpenAI vision judge `gpt-5.4-mini`, is **off**: trials showed it misses the blue-wing colour drift and wrongly calls good beats near-still. A fail holds the stage, a warn is only text, a crash of the gate does not block. Its thresholds are calibrated on very few clips.

**What is NOT proven live** (from `docs/DEVELOPER-TAKEOVER.md` section 10 and the runbooks, and what I saw):
- The takeover guide (status at 95a4cd1) lists as HIGH risk that the Python studio had never produced a real Short end to end. Since then the Moon run did reach MOON_LAYER and PACKAGING on live Temporal with real pods, but with two live failures that were fixed by hand (keyframes save and clips save, both missing default fields) and no final Short is recorded as published. The ant redo is the next live test.
- Never run on a real pod: `tools/film4k/run_shard.py`; one-pod mode (`VIDEO_ONE_POD`); Community abort-and-retry; the VACE repair; chained-beat drift over many beats (the code limits chains to 2).
- The reaper on a real pod (API field names `desiredStatus`, `costPerHr`, `lastStartedAt` are assumed) and its scheduling.
- Moon layer composite and local RIFE interpolation: tested on synthetic clips; the Moon stage did run on the real Moon video, INTERPOLATE never ran in the workflow for real.
- Colour matching between chained beats is not implemented; Temporal replay of old histories against the newest stages was not exercised on a real server.
- The AI Producer deciding on its own (Producer mode is advise-only); Gemini research and Claude research "not exercised in the port" per code comments (the ant run shows Claude research did work live).
- Publishing: there is no working PUBLISH stage in the video workflow; the voice is not the blocker: the owner's ElevenLabs plan is paid (Starter).

---

## 6. Glossary

1. **Beat**: a 5-second piece of a video; one picture and one clip per beat. A 90 s video has 18.
2. **Shot**: the Director's word for a beat or cut; the database stores one row per shot.
3. **Keyframe**: the first still picture of a shot; the clip is animated from it.
4. **Draft / final clip**: a draft is the first video render of a shot; a final is the higher-quality second pass. Camera clips are already final quality.
5. **Hold before run**: the stage stops and asks "approve this spend?" before it spends anything.
6. **Auto / Review / Manual / Producer**: the four modes of each stage (see section 1).
7. **Inspector**: free automatic checks after a stage, using different tools from the stage itself.
8. **Creative Director**: the model that checks the pictures tell the story (shot, paragraph, whole video).
9. **AI Producer**: a model that reviews a stage like a producer and recommends approve / redo / ask; advice only for now.
10. **Fun Camera**: the Wan 2.2 mode that makes a real camera move (push in, pan, drift) instead of a still with a fake zoom.
11. **Chain**: beat 2 starts from the last frame of beat 1; the studio chains at most 2, the third restarts from its own picture.
12. **Pod**: a rented GPU computer (RunPod), billed by the hour, ended automatically.
13. **Lease**: a database row that proves a pod is on purpose; a pod with no lease is killed by the watchdog.
14. **Reaper / kill-timer**: the separate script that kills pods even if the studio is off.
15. **Ledger / CostEntry**: the list of every cost, each in rupees at ₹94 per $ (₹90 for OpenAI). Estimated rows are flagged.
16. **Quality gate**: the free technical check of the finished edit (sound level, frozen or black frames, near-still motion, colour drift).
17. **Plan file**: a finished JSON plan (`docs/shorts/*.json`) that skips the paid planner, writer and director.

---

## 7. Things in this report I could not verify

1. **Live `.env` values.** I did not open `.env`. The model ids are the code defaults, except the text model chain, which I inferred from `GET /api/system` (`openai:gpt-5.4-mini,gemini-3.6-flash`). Whether `ELEVENLABS_COMMERCIAL`, `AICREDITS_FALLBACK_ENABLED`, `VIDEO_PROVIDER_*`, `QUALITY_GATE_ENABLED`, `INTERPOLATE_FPS` and the `GPU_*` limits are set as in the runbook is not verified; the ledger shows pod pictures and pod clips and ElevenLabs voice were used on 25 Nov.
2. **Why the ant video's Creative Director review was booked on Claude.** The code passes no preferred model for that call, yet row `CREATIVE_REVIEW (claude:claude-sonnet-5-5 ...)` is ₹7.02. Older videos booked it on Gemini or Groq.
3. **Whether the ledger's pod charges match the RunPod bill.** The ledger rows are estimates (`estimated = true`, hours x live hourly rate); RunPod's bill lags a few hours and I did not read it. The first Moon pod rental booked 0.0006 h (about 2 s) although that attempt ran about 5 minutes; I did not investigate.
4. **How the two Moon clip files were attached.** The two Fun Camera drafts exist (provider `gpu_pod`, saved 09:38) but the DRAFT_GEN stage is recorded as "Skipped by owner after failure"; the clips were evidently saved some other way (not verified how).
5. **Whether the reaper is scheduled.** A Windows query found no matching task; docs say it is not scheduled. Not conclusive.
6. **The real cost per picture on the pod.** The 18-picture figure uses config numbers (10 s per picture, 900 s start). Observed: 2 pictures took 4.8 to 6.6 minutes of pod time including boot.
7. **Whether Lyria's actual song length covers 90 s with one song.** The code plans up to 3 songs; the song length is a Google property I did not measure.
8. **Whether `approve_paid_first` stays on by default for plan files in the live build**: the ant redo has it on, the Moon video does not (created earlier); the docstring says on by default for plan files.
9. **Hold estimate for KEYFRAMES.** I read that it uses the Gemini image price (`d.pricing.image_inr("1K") * n`) and the ant redo's hold text says about ₹85 for 18 pictures, consistent with 18 x ₹4.74 (₹85.3) and no review term. Whether the owner's live run goes to the pod is shown by the Moon ledger (it did); the hold text is not adjusted for it.
10. **CRITIC / TAKE_SELECTION / AUDIO / ROUGH_CUT_REVIEW / PACKAGING / FINAL_APPROVAL / PUBLISH are placeholders**: shown by the code path (`control.run_stage`) and by the Moon stage summaries for five of them; FINAL_APPROVAL and PUBLISH have never run in a live video I looked at.
11. **Anything about the Autopilot, Dream Shorts and the Java-era videos** except as quoted above: this report covers the video workflow only.
12. **Reaching 4K.** I found no studio stage that outputs 4K: ASSEMBLY's sizes include 3840x2160 in the code table but the wiring offers only 0 (1080p) and 1440 (2K) in documentation; `tools/film4k` (separate scripts) upscales to 2160x3840 with Real-ESRGAN on a pod. Whether `VIDEO_OUTPUT_HEIGHT=2160` works end to end is not verified.
