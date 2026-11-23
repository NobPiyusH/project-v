# How a television works — plan and cost estimate (22 Nov 2026)

**Verdict: STOPPED. Estimate is above the $3 cap and two required lanes do not exist on the Video path. No video was created, nothing spent.**

## What the Video path can do today (read from code)
- Stages: Plan, Research, Concept, Script, Music, Voice, Shot plan, Keyframes, Animatic, Library match, Draft gen, Final gen, Upscale, Assembly.
- Visual lanes actually built: (1) AI keyframe stills (Gemini Nano Banana, or Z-Image on Modal) then (2) AI image-to-video per shot: drafts on **Modal Wan**, finals on **Gemini Omni Flash** (`VideoStage`, `projectv.video.provider-draft=wan`, `provider-final=omni`). (3) NASA free footage (space only, irrelevant here).
- **The Video path does NOT use the GPU broker** (RunPod/Vast). The broker is Dream-Shorts-only (`DreamGpu`). Modal credit is spent (19 Nov) and `/api/budget` shows `gpuCapInr = 0`, so Wan drafts are refused by `budget.requireGpu`.
- **Diagram / animated-graphics lane and Blender lane: not built.** "Four visual lanes per shot" is only a roadmap item (HANDOVER Appendix C, item 7). So electron gun, shadow mask, LCD polarisers, OLED pixels, RF and colour mixing cannot be drawn accurately; AI video would hallucinate them. Panned stills are forbidden (rule 10). Blender: skipped, as instructed.
- Switching drafts to Omni needs `VIDEO_PROVIDER_DRAFT=omni`, read at startup, i.e. a backend restart (forbidden).
- **ElevenLabs:** there is no per-video voice-provider field in `POST /api/videos`. The worker (`voice.py choose_provider`) picks: Chatterbox if available (`PROJECTV_CHATTERBOX=0` in `.env`, so off), else Gemini unless `ELEVENLABS_COMMERCIAL=1`, in which case ElevenLabs when credits cover the video. So it needs `ELEVENLABS_COMMERCIAL=1` in the environment of the **Python worker** and a worker restart (not a backend restart). Credits: ~450 words ≈ 2,700 chars × 0.5 (v4 turbo) × 1.3 headroom ≈ 1,800 of 10,000 free credits, $0.

## Target
~3:00, 16:9, ~450 words (about 18 lines), ~36 shots of ~5 s, 4 beats: what is inside / how it is made / CRT / LCD-LED / OLED / broadcast + colour.

## Shot list by lane
| Lane | Shots | Content | Status |
|---|---|---|---|
| Diagrams/animated graphics | ~20 (~100 s) | gun + deflection + shadow mask + phosphor, LCD stack + polarisers + backlight, OLED pixel, RF chain, RGB mixing | **No lane. Needs new build** (e.g. Manim/HyperFrames worker, plus lane routing in ShotStage) |
| AI video | ~12 (~60 s) | living-room atmosphere, factory/assembly-line "how it's made" | Gemini Omni only (Wan blocked) |
| Blender/3D | 0 | internals fly-through | skipped |
| Real footage | 0 | NASA only | n/a |

## Cost estimate (studio's own pricing, calibration x1.5)
| Item | Basis | USD |
|---|---|---|
| Keyframes | ~36 x $0.0336 x 1.5 | ~1.8 |
| AI video, Omni 360p | 60 s x 1,931 tok/s x $12/M x 1.5 | ~2.1 (720p: ~6.3) |
| Text (Plan/Script/Concept/Director, Claude+Gemini) | from prior videos (~Rs 300 for a Short, incl. media) | ~1.0 |
| Music (Lyria) | 1 song | ~0.12 |
| Narration | ElevenLabs free credits | 0 |
| GPU | broker unusable by this path; Modal cap 0 | 0 |
| **Total, AI-only 60 s of motion, 360p** | | **~5.0** |
| If all 180 s were AI video (no diagram lane) | 180 s x $0.035 | ~7.5 + 1.8 + 1.1 = **~10.5 (720p ~ 24)** |

Gemini monthly cap remaining: Rs 434 (about $4.6), so even the cheapest variant would nearly exhaust it.

## Decision needed from CTO / owner (what would make this runnable)
1. Raise the cap for this test (to ~$6 at 360p, ~$11 at 720p), or accept a much shorter cut.
2. Build the missing diagram lane (this is the real gap; without it the technical mechanics cannot be explained accurately). Needs a code change + backend restart, which I was told not to do.
3. Approve a Python-worker restart with `ELEVENLABS_COMMERCIAL=1` (non-commercial free plan, test only).

---
# REVISION 2 (22 Nov 2026, CTO/owner creative rules)
Story, not syllabus: hook = "where does the picture come from?", one thread: follow the picture backwards from the screen into the wall. No "in this video", no lists, no definitions first. Conversational, short sentences, a little humour.
Visuals always moving: diagram shots get camera moves/parallax/zoom and macro-to-cosmic scale changes; cut between diagram and photoreal AI shots; fast on action, slow hold on reveals; cut to the music; one idea per shot; no static labelled slides, no panned stills.
Mix: photoreal AI shots (living room, repair-bench, glass/phosphor macro, factory floor, rooftop antenna) alternating with diagrams. Video path has no stock footage lane (NASA only).
Constraints unchanged: 16:9, ~3 min, 2K target, $3 hard cap, backend not restarted.
Cost: revision makes it WORSE, not better (more photoreal AI video, 2K). Still over the cap; see message. Waiting for CTO go before creating anything.

---
# REVISION 3 (broker route live): see the message sent to the CTO; summary
Realistic total ~$2.4 (range $1.5-2.6 at pod $0.5-0.9/h), hard worst case ~$4.3. Route: FLUX keyframes + Wan 480p drafts + 720p finals on one warm pod, 10 diagram shots free (Manim 2560x1440), ElevenLabs free credits, Lyria music, text on Claude/Gemini. Script ~456 words, 26 shots (10 diagram, 16 photoreal AI). Waiting for CTO go.
