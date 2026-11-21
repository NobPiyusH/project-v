# SVI scenes: what it is and the first paid A/B (needs the owner's OK)

Branch `claude/svi-scenes-b4ef1e`. Nothing here has run on a real GPU yet. **Nothing in this runbook may run until
the owner approves the spend below.**

## What it is

A fourth clip method for Dream Shorts, next to *separate clips*, *last-frame chain* and *first-last frame*. Off by
default.

- A Short has **1 to 3 scenes**. Each scene is **one continuous shot** of **10, 15 or 20 s** from **one picture**.
  One scene = a single long shot (impossible / architecture / walk-through / dreamcore). 2 to 3 scenes = a story Short
  (3 × 20 s ≈ 1 minute).
- A scene is made of **5 s beats** (81 frames at 16 fps, 480×832). Each beat has its own motion line. 10 s = 3 beats,
  15 s = 4, 20 s = 5. 20 s is the limit because the bench showed drift after that.
- Between scenes: **hard cut** (default) or a **0.5 s dissolve**.
- The recipe is the one from the 21 Nov bench (`C:\dev\bench\svi_test\RESULTS.md`): ComfyUI v0.45.0, KJNodes
  `aa2b5ea`, node `WanImageToVideoSVIPro`, the SVI 2.0 Pro HIGH/LOW LoRAs at 1.0 on top of LightX2V, 4 steps,
  CFG 1, 2+2 split, uni_pc_bh2, shift 3, 3 motion latents, 12-frame linear cross-fade, no colour correction.

### How it flows through a Short

| Step | What changes with SVI scenes |
|---|---|
| Write | The writer returns scenes, each with an opening view and one motion line per beat, plus one style line for all scenes. Unfiltered: a blank line starts a new scene and each line is a beat, words used exactly. |
| Pictures | One picture per scene (beat 1). A new picture makes every beat of that scene stale. |
| Clips | One pod job per scene renders all its beats in one ComfyUI graph, back to back in the clips phase (same batch order as clips). Each beat is saved as an MP4 plus its latent. |
| Redo / Retry | Redoing beat k renders that scene again from beat k to the end, starting from the saved latent of beat k−1. Earlier beats are kept. A failed scene fails only its own beats, and Retry picks up from the first missing beat. |
| Finish | The laptop cross-fades each scene's beats into one shot, then the Finish runs as it always does (RIFE to 24 fps, upscale, look, music, beat sync). Scene order is kept. |

Auto, Review and Manual work on every step exactly as before, because SVI uses the same steps and gates. A failure
waits for Retry and never ends the Short.

## The paid A/B (to run only after the owner approves)

**Question:** is a 3-scene, 60 s SVI Short clearly better than the same Short made with last-frame chains?

**Setup (one shared pod, one session):**
- **Short A (today's method):** look *Impossible architecture*, Clip method *last-frame chain*, chains of 4
  (`chainShots: true, chainGroup: 4`, API only, because the form offers pairs and threes), 12 shots. That gives
  3 scenes of 4 chained clips each, about 20 s per scene.
- **Short B (SVI):** same idea and look, Clip method *SVI scenes*, 3 scenes × 20 s, hard cut.
- Same idea text, your music track (free), Mode Review.
- Make B's pictures first, then give A the same 3 scene pictures (redo A's pictures by hand) so only the clip method
  differs.
- Judge by eye on the phone, side by side: smoothness inside a scene, restarts, colour drift, and whether 20 s holds
  together.

**What to look for in the logs (first real run of new code):**
1. The first scene job logs `SVI ... from huggingface` twice and then `SVI ready in N s`. Expect about 2 to 3 minutes.
2. Every beat comes back with a `.latent` file next to its MP4 in storage.
3. Try a redo of beat 3 of scene 2. Only beats 3 to 5 of scene 2 should run again.

### Cost estimate

The live GPU price is read when it runs (rule 7). These figures use the bench's measured times and its price.

| Item | Pod time | Notes |
|---|---|---|
| Pod boot (pip, ComfyUI, ~42 GB weights) | 10–15 min | bench: 478 s download + ~3 min setup |
| Pictures: 3 scenes (shared) | ~2 min | Z-Image load + 3 pictures |
| Short A: 12 chained clips | 9–15 min | bench 44 s/clip on a 5090; studio estimate 76 s |
| SVI setup (KJNodes + 2 × 1.23 GB LoRAs + restart) | 2–3 min | first scene job only |
| Short B: 15 SVI beats | 11–19 min | bench ~42 s/beat on a 5090; studio estimate 76 s |
| **Total** | **~35–55 min** | |

- At the bench's RTX 5090 community price ($0.69/h, ₹94/$): about **₹40–60**.
- At an RTX 4090 community price of about $0.34/h: about **₹20–30**.
- Writer, quality checks and music: under ₹10 (music is your own track, free).
- **Suggested cap: ₹80.** The studio shows its own clips estimate and asks for the cost OK before any clip runs.
  Note: the studio's estimate assumes 76 s per beat, so it will be higher than these measured numbers.

## Before the A/B

1. Merge or check out this branch, then restart the control plane, media worker and dashboard. Their changes are a
   migration (V39), the new form options and the new pod file.
2. The pod worker file goes up when the pod is rented, so a pod that is already running does not have the scene job.
   Start the A/B on a fresh pod.
3. The broker must be on (`projectv.dream.gpu-provider=broker`). SVI refuses to run on the Modal route.

## Known limits and follow-ups

- Tested only with fakes and unit tests: pod graph, validation, the scene path in the media worker, real-FFmpeg
  assembly, and the Postgres flow from write through finish. The real ComfyUI graph has not run since the bench.
  `SaveLatent` and `LoadLatent` are new compared with the bench, so the redo-from-a-beat path is the riskiest part.
  Step 3 of the log checks above is there to test it.
- SVI is always 480×832. The 720p setting does not apply because the recipe was proven at 480p only.
- Pod GPU cost is booked once per rental by the broker, as for every pod clip. It is not split per beat in the cost
  ledger. Each beat gets its own Provenance row, and the routing decision records the method (stage `DREAM_CLIPS_SVI`).
- "Choose the path" episodes continued from an SVI Short use normal clips.
- The migration is numbered V39. If another branch also adds a V39, rename one before merging.
