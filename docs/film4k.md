# Film pipeline (4K game-level films on rented pods): how it works, what it costs, what we learned

Built and measured on 24 Nov 2026. Code: `tools/podkit/` (rent, boot, ledger, guards), `tools/film4k/` (storyboard, shard runner,
assembler, camera move), `tools/model_ab/` (same-prompt model comparison), `tools/gpu_lease/` (watchdog lease), `tools/ops/` (Wi-Fi keep-alive).
Tests: `uv run --group dev python -m pytest tools/podkit tools/film4k tools/gpu_lease` (24 tests, no network, no pods).

## The chain
1. **Storyboard** (`tools/film4k/storyboard.json`): 15 scenes x 3 beats of 5 s, one world, one creature design. `{c}` in a keyframe prompt = the creature.
2. **Keyframes**: Seedream v4 on RunPod's public endpoint, $0.027 each, photographic wording (muted palette). Look at them before any GPU.
3. **Shards** (`run_shard.py`): one pod per shard; Wan 2.2 fp8 + SVI, 3 beats per scene, each scene from its OWN keyframe; Real-ESRGAN x4 to 2160x3840 on the same pod.
4. **Assembler** (`assemble.py`, laptop): 0.5 s cross-fade between beats, post camera move, ONE RIFE pass to 48 fps at 1.25x speed, grade, HEVC, concat.

## Measured (RTX 4090 Secure $0.89/h, A6000 $0.69/h)
| Step | Measured |
|---|---|
| Pod boot (41 GB weights + ComfyUI + KJNodes + SVI LoRAs + ESRGAN) | about 7 min |
| SVI scene (3 beats of 81 frames at 720x1280) | about 10.5 min on a 4090 (about 3.5 min per beat) |
| ESRGAN x4 per 5 s beat | 3-5.5 min |
| One scene end to end | about 18-20 min |
| Cost per scene | about $0.28-0.31 (Secure 4090) |
| Laptop (RTX 3050): RIFE at 4K, one scene | about 8 min |
Act 1 of the 3-minute film (8 scenes, 3 pods): about $2.93 of pods + $0.22 keyframes.

## What went wrong, and what the code now does about it
| Problem | Fix |
|---|---|
| Boot script ended in a bare `wait` (ComfyUI never exits), so "BOOT COMPLETE" never printed: a 25 min idle pod | `boot.py` waits only for the download PID; a test fails if a bare `wait` returns |
| Lease helper died silently when Docker was off (the studio watchdog was off too) | `lease.py keep` retries, then exits 2 with "NO LEASE"; `guard.start_guards` confirms the lease row and returns False: the caller terminates the pod |
| A pod's 75 min safety cut a shard mid-scene | the runner resumes (finished scenes are skipped); the ledger sizes deadlines to the cap |
| Several pods at once could pass the approved total | `podkit/ledger.py`: each pod reserves rate x deadline before renting; a reservation over the cap is refused |
| Camera barely moves with SVI whatever the prompt says | `camera.py`: push-in + drift/tilt + shake on the 4K plate before RIFE |
| Post "zoom out" crashed ffmpeg (`scale=eval=frame` with a shrinking size) | every scene pushes in; a test enforces it |
| Beats do not join exactly (SSIM 0.43-0.76 at the seams) | cross-fade inside a scene; hard cuts only between scenes |
| 81 frames x 2880x5120 floats = about 14 GB for ESRGAN | upscale in 3 slices of 27 frames |
| SVI drifts after about 5 beats; 8 chained beats lost camera motion and the creature melted | 3 beats per scene, a fresh keyframe per scene |
| One bad host (no CUDA) and stock gaps | the retry runner re-rents only when that costs nothing |

## VRAM (what Wan 2.2 14B needs)
About 24 GB peak at 720x1280 with fp8 experts (each about 14 GB, one in VRAM at a time), text encoder 11 GB while encoding, frames 6-9 GB. A 4090 or 3090 works
(3090 at 640x1136), a 48 GB card is not faster, 16 GB needs GGUF/offload (untested), 12 GB no. The pod needs 48 GB of system RAM.

## Status and limits
- `tools/film4k/run_shard.py` was rebuilt on podkit from the working script and is unit-tested only. It has NOT run on a real pod: the first real run must be a smoke test (one scene).
- Prices and stock change: read them live (CLAUDE.md rule 7). Community 4090s were out of stock all day on 24 Nov.
- Real-ESRGAN from 720p is sharp but slightly painterly; 3 minutes of one world can feel repetitive (Act 2 has a second location, an interior and a close-up).
- No audio yet (free stock music + effects; Lyria was blocked by a Google billing 403 on 24 Nov).
