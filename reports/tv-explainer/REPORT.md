# How a television works: run report (22 Nov 2026)

**Result:** `reports/tv-explainer/how-television-works.mp4` (2560x1440, 24 fps, H.264 + AAC, 180.0 s, 194 MB), plus `how-television-works.srt` (89 captions).
Video id `d3e53cd1-520e-4dd3-8ecb-b6ed0714aa93` (COMPLETED 21:06 UTC). Hard cap for this test: **$4.20 for both videos together** (raised by the owner: $3 -> $4 -> $4.20).

**I have NOT watched the video or listened to it.** Everything below about quality comes from stage summaries, the Inspector and file probes, not from eyes and ears. The owner should watch it before anything else is decided.

## What ran
Prompt with the owner-approved 14-line script (456 words) -> Plan, Research, Concept, Script (Review), Music (Lyria), Narration (ElevenLabs), Shot plan (Review), Keyframes (FLUX on a pod), Animatic, Draft gen (Wan 720p on a Vast pod + 11 diagrams drawn on the laptop), Final gen (skipped), Upscale (skipped), Assembly.

Shots: 32 over 180 s = 11 diagram (Manim, 2560x1440), 21 AI video (Wan 2.2 on a rented pod, 1280x720, 27 clips because 6 shots are longer than one 5 s clip), 0 stock.
Music: Lyria, 117 BPM (brief said ~90; owner approved it by ear). Narration: ElevenLabs "George", eleven_v4_turbo, 918 credits, 171 s of speech, all 14 lines passed the transcript check.

## Real cost (from the ledger and broker)
| Item | INR | USD (94/USD) |
|---|---|---|
| This video, Claude + Gemini (text, diagram specs, music) | 140.6 | 1.50 |
| This video, GPU rentals: Vast 125.0 + RunPod 29.5 | 154.5 | 1.64 |
| ElevenLabs | 0 (918 of 10,000 free credits) | 0 |
| First attempt b3989771 (paused, Gemini voice by mistake) | 57.4 | 0.61 |
| **Total both videos** | **~352.5** | **~3.75** (cap $4.20) |

(An earlier "about $3.4" I gave was an estimate; the ledger says $3.75.) Pods: Vast 62017399 at $0.505/h (18:16-20:11 UTC, 27 clips), one short Vast pod for the keyframe redo, RunPod iglffucup03xr5 at $0.89/h (about 21 min, mostly stuck, see problems). **All pods released at the end: /api/gpu/pods empty, no open leases, watchdog sweeps show 0 studio pods.** Measured speed: about 213 s per 720p clip after a warm start of ~18 min (the estimate assumed 357 s).

## Time per stage (UTC, wall clock)
Plan 8 s, Research 16 s, Concept 12 s, Script 56 s (+ my review edit), Music 66 s, Narration 74 s, Shot plan 5,363 s (failed, timeout) then 2,017 s (retry, OK), Keyframes 3,849 s (mostly pod boot / disk problem), Animatic 64 s, Library match 9 s, Draft gen 412 s (failed on cap) + 6,968 s (27 clips) + 1,681 s (diagram redo and approval), Final gen skipped, Upscale skipped, Assembly 364 s. Wall time start to finish 6 h 49 min, most of it waiting on fixes.

## Stock lane and licences
3 stock shots were filled (a screwdriver on a back panel, a TV carried up stairs, a rooftop antenna) but the owner rejected them as wrong content and they were switched to AI. No stock clip is in the final cut, so **no stock attribution is needed**.
**Voice licence:** ElevenLabs free plan is non-commercial; a paid plan is needed before any publishing (owner memory, 22 Nov).

## Decisions and things skipped (owner can reverse)
- **Finals skipped:** hybrid 12-step 720p finals would cost ~$1.27 + boot (would have put the total over the cap). Clips are the 4-step 720p drafts, "keep the draft" set on all 21 AI shots. Decide on finals for hero shots only after watching the cut.
- **Upscale skipped** (Modal GPU cap is 0): no SeedVR2 and no 16 -> 24 fps smoothing. Assembly scales 720p to 1440p with Lanczos + light sharpen; 12 shots were slowed 1.04x-1.28x to fill their slots (no smoothing, so slowed AI shots may look slightly slow or stepped).
- Stage Inspector on diagram clips still shows black/frozen findings: accepted by the CTO (fades). My local re-check of the new diagram takes: motion fixed on shots 7, 8, 10, 14, 21, 28, 29; shots 20 and 22 look unchanged (about 5 s frozen; 22 is the OLED black shot, black probably intended); short black fades (0.3-0.7 s) on 7, 15, 28, 29.
- Assembly Inspector: passed, one warning (peak -0.4 dBTP).

## Problems hit (all handed to the CTO, mostly fixed in code)
1. First video's narration fell back to **Gemini**: ElevenLabs 429 (3 parallel requests on a free plan that allows 2). Fixed; second video is ElevenLabs. First video stays paused.
2. The writer shortened my approved script; I restored the 14 lines by hand via the line-edit API in the review window.
3. Shot plan hit the 8,000-token output ceiling (3 retries), then a 76-minute activity timeout; raised to 16,000. After a restart the workflow wedged on a replay error (worker stock_fetch returned fields Java rejected); fixed.
4. Manual mode does not hold a stage; it runs it, then waits. Keyframes started the moment I approved Shot plan.
5. Pod start (8-18 min) exceeds the 5-min activity limit in the keyframe request and the shot-redo workflow: retries kept timing out and (for a while) re-renting. Fixed with join-on-retry.
6. First RunPod pod: disk full (Z-Image 31 GB + Wan 45 GB prefetched on 80 GB), so FLUX + the text encoder could not download; ~21 min wasted ($0.31). The CTO deleted the unused Z-Image cache. The 4 opening keyframes failed in that phase and were redone on a second pod.
7. Monthly GPU total cap ($8) was used up mid-run; raised to $20; Draft gen needed a Retry.
8. Draft gen estimate used a worst-case $2.0/h (showed $5.36); real cost was ~$1.0.
9. Diagram clips first rendered nearly static (frozen 3-6 s per shot): diagram lane motion upgraded by the CTO and redone free.

## What looked weak / not verified
- Not watched or listened to by me; AI clip quality, diagram legibility, music/voice sync and the 12 slowed shots are unchecked.
- Diagram shots 20 and 22 probably still static; the 117 BPM music vs the 90 BPM brief.
- 4-step drafts upscaled by Lanczos will be softer than true 2K; the AI upscaler was not available.
- Final cut is 180.0 s with narration of 171 s; ending hold is short.
