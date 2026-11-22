# Queue after the television video (owner d3e53cd1), 22-23 Nov 2026

Each item starts only after the television video ends and the owner says go. Nothing here spends money until he approves it. Estimates, not measurements.

1. **Housekeeping (free):** merge branch `claude/cost-cuts-a99b7a` into local main (guards ON, flags OFF); backend restart; confirm no pod left running.
2. **ElevenLabs-only voice lock** (per-video, only for ElevenLabs videos): a voice failure or short credits waits for the owner, never falls back to Gemini; show credits left vs needed.
3. **One GPU pod per video**: keep the pod across stages (keyframes, drafts, finals) instead of re-renting each batch (boot 14-21 min, ~$0.1-0.2 each); watchdog stays the safety net.
4. **Quality gate (Sonnet vision, ~₹0.5 per check):** reject keyframes and stock clips that do not match the shot; tested first on the 21 television pictures with the owner as judge. CLIP/SigLIP stock ranking later if needed.
5. **Z-Image default to FLUX** for pod pictures, so Z-Image is never touched unless asked.
6. **Move files C: to E:** uv/model caches, Docker data, reports, Downloads (copy, verify, junction). Owner approved; services stopped only when nothing runs.
7. **Blender test scene (3-6 h):** one 20 s scene (satellite orbit or CRT tube), Blender 5.3 at `E:\Blender\blender.exe`, low-quality laptop preview; owner judges by eye. If liked: cloud render on the pod, templates, 3D lane behind a switch.
8. **Upscale test (~$0.3):** five shots at 480p upscaled to 720p next to native 720p; owner judges by eye. No upscale stage exists yet.
9. **Later, owner to pick:** five Shorts in a row (reliability), YouTube publishing (paid ElevenLabs or another voice first), shared Producer (AI orchestration) with hard guardrails, `ai.dreamscape00`-style AI Reel.

Owner to-do: rotate pasted keys (Vast, E2E, RunPod, ElevenLabs, Pixabay, Vast TOTP seed); push waits for his "push".

## Owner decisions, 23 Nov 2026 (his words in chat)
1. Housekeeping: yes.
2. Voice lock: only for videos that have narration (ElevenLabs-narrated).
3. One pod per video: yes. 4. Quality gate with Sonnet: yes. 5. FLUX default: yes; Z-Image stays only for "lonely" or where the CTO decides it is needed (optional).
6. C: to E: move: yes. 7. Blender test scene: yes. 8. Upscale test: yes. 9. Later.
10. Overnight: move the code base from Java to Python only. The owner will say when. See risk note below.
11. Direction: no rented pods; serverless GPU with the models built in (RunPod Serverless, Modal or similar), model files baked into the image or a network volume.
12. Direction: plan-then-make pipeline; one Director writes a strict validated plan; every output stored with a fingerprint of its inputs; a changed shot redoes only itself and what depends on it.
13. Direction: small on purpose: one app, one database, one file store, one GPU route, one screen per stage, one proven provider per job, A/B tests, no pile of fallbacks.

CTO notes: 10-13 are one program (a Python-only studio on serverless GPU). Do it as a staged migration on a branch, never a big-bang rewrite: prove serverless on one Short first (item 11), then move one stage at a time. Risk HIGH if done unattended in one night; agree scope and tests before starting.

## Owner follow-up, 23 Nov 2026
- Item 5: run an A/B of FLUX.2 klein vs Z-Image Turbo on the "lonely" look (same prompts, owner judges by eye, a few cents on a pod already running), THEN decide the default. Until then FLUX stays the default for Video; Z-Image stays for Dream Shorts "lonely".
- Item 10: "do as needful, but later the code base will increase": agreed, the cost of the Java-to-Python move grows with the codebase, so start soon. Plan: right after the television video, write the migration plan (scope, order of stages, tests, stop rule, branch), get the owner's go, then migrate one stage at a time, old path kept until the Python one passes its tests and he approves.

## Added 23 Nov 2026
- **Earshot test** (https://github.com/Leonxlnx/earshot, MIT, CPU-only sound-effect generator, 922 designs; README read only, not installed): after the television video, small test on 5 shots: compare its effects with the current SFX step, owner judges by ear; also try its "listen/timeline" to check music/voice mix. Base install ~350 MB, listening models ~1.1 GB download / 2.2 GB on disk (put on E:). If preferred: optional SFX source behind a switch.
- **Fix list noted today:** Director diagram-spec callout fields (x,y = text, tx,ty = target) were swapped in some specs; DIAGRAM_SPEC parse failure falls back to a text card (shot 20); mojibake "Â°" in text from an upstream encoding bug; AI creative review wrongly flags diagram shots as "missing image prompts"; Keyframes summary label fixed (6dd3cb6).
- **Music/voice sync check** on the final cut: loudness voice vs music, silence gaps, music ending with the voice, caption timing; owner then listens.
- **HyperFrames test** (https://github.com/heygen-com/hyperframes, Apache 2.0, HTML/CSS/JS to deterministic MP4, Node + headless Chrome, agent skills; README read only, not installed; the Studio app/Pro plan is not needed): after the television video, rebuild the two weakest diagrams (shot 14 overlaps, shot 21 tiny labels) as HTML at 2K, compare by eye with the Manim versions, and time a 2K render. If clearly better: new diagram + caption/overlay lane (Manim kept for exact math drawing). Owner approved queueing, 23 Nov 2026.
