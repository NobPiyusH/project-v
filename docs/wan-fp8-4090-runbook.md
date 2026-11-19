# Wan 2.2 I2V fp8 on one RTX 4090: benchmark runbook

Goal: find out whether Wan 2.2 I2V A14B in **fp8** on a rented **RTX 4090 (24 GB)** gives clips the owner cannot tell
apart from today's Modal clips (`workers/modal_apps/wan.py`: Diffusers bf16 + lightx2v 4-step LoRAs, "fast", 480p).
Quality first (rule 10): fp8 ships only if the owner cannot tell it apart by eye.

**Nothing here may run without the owner's explicit yes** (it rents a pod = spends money, rule 2).

## Kit

| File | Runs on | What |
|---|---|---|
| `workers/bench/wan_fp8/export_dream_inputs.py` | laptop | read-only export of one Dream Short: keyframes, prompts, seeds, sizes + Modal baseline clips |
| `workers/bench/wan_fp8/pack.py` | laptop | bundles inputs + pod script; makes exec-pod upload chunks; joins base64 parts on the way back |
| `workers/bench/wan_fp8/bench_wan_fp8.py` | pod | downloads weights, runs ComfyUI, renders, writes MP4s + `results.json`; `--dry-run` without GPU |
| `workers/bench/wan_fp8/blind_ab.py` | laptop | random left/right side-by-sides + hidden key for a blind judgement |
| `workers/tests/test_bench_wan_fp8.py` | laptop | unit tests (`uv run -q pytest -q workers/tests/test_bench_wan_fp8.py`) |

## Chosen route: ComfyUI (pinned `v0.45.0`) + Comfy-Org fp8_scaled experts, driven over ComfyUI's local API

Why not Diffusers + torchao/quanto fp8: the only Diffusers weights are bf16 (2 x 57 GB shards for the experts, ~69 GB
download with the text encoder), they must be loaded in bf16 on the CPU (~57 GB host RAM) and quantized on the pod
every start, and the LoRA must be fused before quantizing; none of that has been proven on 24 GB. ComfyUI's
fp8_scaled experts are the community-standard 24 GB path: 14.3 GB per expert, ComfyUI moves the idle expert to host
RAM by itself, and our exact LoRA files load natively (the lightx2v 1022 files use ComfyUI's `diffusion_model.*`
`lora_down/lora_up/diff_b` keys, checked in the safetensors header). Download is 41.6 GB instead of ~69 GB.

Fairness (same as Modal "fast"): same LoRA files and strength 1.0 on both experts; 4 steps; CFG 1.0; UniPC bh2 with
flow shift 3.0 (Diffusers' `scheduler_config.json` for this model); high-noise expert for steps 0-2, low-noise for
2-4 (computed from Diffusers' `boundary_ratio` 0.9: timesteps 1000, 900.1, 750, 500); same prompt and seed (taken
from each baseline clip asset's own record); 480x832, 81 frames, 16 fps; keyframe resized with PIL LANCZOS; H.264
CRF 12 preset slow yuv420p. The text encoder stays fp16 by default so the fp8 experts are the only variable
(`--text-encoder fp8` tries the 6.7 GB fp8 encoder as a second variable).
Known, unavoidable differences: ComfyUI draws the noise on the CPU and Diffusers on CUDA, so the same seed gives a
different (equally random) motion: compare quality (detail, mush, flicker, warping), not frame-for-frame identity;
ComfyUI restarts UniPC's history at the expert switch.

### Weights (Hugging Face API, 20 Nov 2026; all Apache-2.0)

`Comfy-Org/Wan_2.2_ComfyUI_Repackaged` now redirects to **`Comfy-Org/Wan-2.2`** (revision `5df3a4a3`), which is
why the earlier unauthenticated call failed.

| File | Repo | Bytes |
|---|---|---|
| `split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` | Comfy-Org/Wan-2.2 | 14,294,742,832 |
| `split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` | Comfy-Org/Wan-2.2 | 14,294,742,832 |
| `split_files/text_encoders/umt5_xxl_fp16.safetensors` (default) | Comfy-Org/Wan-2.2 | 11,366,399,385 |
| `split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` (option) | Comfy-Org/Wan-2.2 | 6,735,906,897 |
| `split_files/vae/wan_2.1_vae.safetensors` (A14B uses the 2.1 VAE) | Comfy-Org/Wan-2.2 | 253,815,318 |
| `wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors` | lightx2v/Wan2.2-Distill-Loras | 634,645,944 |
| `wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors` | lightx2v/Wan2.2-Distill-Loras | 739,472,104 |

Total download: **41.6 GB** (fp16 text encoder) or 37.0 GB (fp8 text encoder). The script checks each file's size.

## Inputs: "Impossible Commute" (all 4 clips exist)

"Tilt Up the Ramp" was cancelled mid-clips, so the completed Short `15cc5947-4099-4e90-8f0f-4b7ce2a731f5` is used.
All clips: 480x832, 81 frames, 16 fps, Modal H100 "wan2.2-i2v-a14b fast".

| Clip | Seed | Keyframe asset | Modal baseline clip asset |
|---|---|---|---|
| shot01 | 280202160 | 3497ac1e-c4c4-4527-a763-83966f6bcb9a | 40f0859b-be11-49f9-b4f1-8f23710bc2cf |
| shot02 | 187268125 | ce9cc9eb-eb62-44d7-b9ec-a47d69cf6cc6 | caac86d4-abf4-413f-bac2-2db9dec37432 |
| shot03 | 1344812779 | 8366fc05-4516-40ab-926d-1441947a2b00 | 0e9962cd-eef0-4738-aba5-e39939b5acd1 |
| shot04 | 1813518289 | 2dff4a23-e5a2-492d-98b5-f863b838659b | b87eee2a-e13a-4b8e-8f09-ed558212689a |

Baseline clips play in the dashboard or at `http://127.0.0.1:8080/api/assets/<clip asset>/content`.

## Expected time and cost (assumptions; re-read the price live before creating)

- Pod setup (clone ComfyUI, pip): ~3-5 min. Download 41.6 GB: ~4 min at 200 MB/s, up to ~15 min on a slow host.
- First clip: ComfyUI reads ~40 GB from disk into RAM/VRAM, +1-3 min. Each clip: ~2-3 min on a 4090
  (4 steps at 480x832x81 with one expert swap, plus VAE decode), so 4 clips ~10-12 min.
- Total ~25-35 min of pod time; the guard caps it at **60 min**.
- At the quoted ~$0.34/hr Community: **~$0.15-0.20**, worst case at the cap **~$0.35** plus a few cents of
  container disk. Price and stock change; quote the live `list-gpu-types` / `get-gpu-type` number to the owner.

## Steps for the coordinator (only after the owner's yes)

### 0. On the laptop: prepare inputs (read-only, no cost)

```powershell
uv run python -m workers.bench.wan_fp8.export_dream_inputs --dream 15cc5947-4099-4e90-8f0f-4b7ce2a731f5 --out C:\dev\bench\wan_fp8\inputs
```
```powershell
uv run python workers/bench/wan_fp8/bench_wan_fp8.py --manifest C:\dev\bench\wan_fp8\inputs\manifest.json --dry-run
```
```powershell
uv run python -m workers.bench.wan_fp8.pack pack --inputs C:\dev\bench\wan_fp8\inputs --out C:\dev\bench\wan_fp8\bundle
```
(`C:\dev\bench` is outside the repo; move to the media SSD once it exists, rule 12.) The bundle is ~4.7 MB, 7 exec-pod
calls.

### 1. Make the self-terminate command (PodSelfStop, 60 min max, 15 min idle)

The pod's "job" just waits until the coordinator marks it done; the guard terminates the pod at the deadline, after
15 idle minutes, or right after the job ends. Generate the exact cmd with the merged Java class (needs a compiled
control plane; does not touch the running one):

```powershell
@'
import com.projectv.gpu.PodSelfStop;
var argv = PodSelfStop.wrap(java.util.List.of("bash", "-c", "until [ -f /tmp/projectv-done ]; do sleep 10; done"), 60, 15, java.time.Instant.now());
System.out.println(new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(argv));
/exit
'@ | & "C:\Program Files\Eclipse Adoptium\jdk-21.0.13.9-hotspot\bin\jshell.exe" -q --class-path "C:\dev\project-v\control-plane\build\classes\java\main;$((Get-ChildItem -Recurse $env:USERPROFILE\.gradle\caches\modules-2\files-2.1\com.fasterxml.jackson.core\jackson-databind -Filter *.jar | Select-Object -First 1).FullName)"
```
(If Jackson is awkward, print `PodSelfStop.asShellString(argv)` and pass it as the `args` string instead.)
Create the pod within a minute or two of generating it: the deadline is baked in as epoch seconds.

### 2. Create the pod (billable from here)

Read live stock and price first (`get-gpu-type` "NVIDIA GeForce RTX 4090"), state the hourly price, then
`create-pod` with:

```json
{
  "name": "projectv-wan-fp8-bench",
  "cloud": "COMMUNITY",
  "gpu": {"id": "NVIDIA GeForce RTX 4090", "count": 1, "minCudaVersion": "12.8", "minRamPerGpu": 48, "minVcpuCountPerGpu": 8},
  "image": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
  "disk": 80,
  "cmd": ["<argv from step 1>"]
}
```

- 80 GB container disk: 41.6 GB weights + ComfyUI/pip (~6 GB) + frames/clips; no network volume (nothing to keep).
- 48 GB host RAM so both idle experts (28.6 GB) + the fp16 text encoder stay in RAM. If no 48 GB host is in stock,
  32 GB works with `--text-encoder fp8` but ComfyUI may reload from disk (slower; note it in the result).
- torch 2.8 matches the Modal image. No ports: everything goes through `exec-pod` (nothing on the laptop is exposed).
- Record the pod id; this conversation created it, so it may terminate it.

### 3. Upload the bundle (exec-pod; retry 404/409 for ~30 s while the container starts)

For each `bundle\exec_chunks\NNN.json` in order: `exec-pod` with `command` = that JSON array. Then:

```
["sh","-c","cd /workspace && base64 -d bundle.b64 > bundle.tgz && tar -xzf bundle.tgz && ls -la bench_in bench_in/images"]
```

(Alternative: `runpodctl send` on the laptop + `runpodctl receive <code>` on the pod, once runpodctl is installed on
the laptop; it is not installed today.)

### 4. Install and run (background, logs to files)

```
["sh","-c","cd /workspace && nohup sh -c 'git clone --depth 1 --branch v0.45.0 https://github.com/comfyanonymous/ComfyUI && pip install -q -r ComfyUI/requirements.txt huggingface_hub hf_transfer imageio-ffmpeg && python bench_in/bench_wan_fp8.py --manifest bench_in/manifest.json --dry-run && HF_HUB_ENABLE_HF_TRANSFER=1 python bench_in/bench_wan_fp8.py --manifest bench_in/manifest.json --weights /workspace/weights --comfy /workspace/ComfyUI --out /workspace/out' > /workspace/bench.log 2>&1 &"]
```

Check that pip did not replace torch (`python -c "import torch;print(torch.__version__, torch.cuda.is_available())"`).
Follow with `["sh","-c","tail -n 20 /workspace/bench.log; nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv"]`
every minute or two. Each finished clip prints one JSON line; `/workspace/out/results.json` is rewritten after each
clip. If a clip fails, `/workspace/out/comfy.log` has ComfyUI's error.

### 5. Bring results back

- `results.json` and `comfy.log` tail: `["cat","/workspace/out/results.json"]` (small).
- Clips (~3 MB each). With runpodctl on the laptop: `runpodctl send /workspace/out/shot01.mp4` on the pod (exec),
  `runpodctl receive <code>` on the laptop. Without it, in 700 kB slices:
  `["sh","-c","tail -c +$((K*700000+1)) /workspace/out/shot01.mp4 | head -c 700000 | base64 -w0"]` for K = 0, 1, ...
  until empty; save each stdout as `parts\shot01\KKK.b64`, then
  `uv run python -m workers.bench.wan_fp8.pack join --parts parts\shot01 --dest C:\dev\bench\wan_fp8\fp8\shot01.mp4`.
  Check the size against `ls -l /workspace/out/` before teardown.

### 6. Teardown (always, even after a failure)

1. `["touch","/tmp/projectv-done"]` (the guard then self-terminates), and also `delete-pod` with the recorded id.
2. `list-pods`: confirm no pod named `projectv-*` remains. A stopped pod still bills its disk: terminate, never stop.
3. Read the pod's billing (`list-pod-billing`) and book the real cost (CostEntry, rule 6) when the studio side exists.

## Judging: pass/fail

```powershell
uv run python -m workers.bench.wan_fp8.blind_ab --inputs C:\dev\bench\wan_fp8\inputs --fp8 C:\dev\bench\wan_fp8\fp8 --out C:\dev\bench\wan_fp8\ab
```

- [ ] **Owner, blind, by eye**: for each `shotNN_ab.mp4`, which side is better, or "can't tell". Open `key.json` only
      afterwards. **Pass** = the owner cannot pick the Modal side reliably (e.g. no more than 2 of 4 right, and never
      "fp8 is clearly worse"). Watch detail late in the clip, mush, flicker, warping, colour shift.
- [ ] All 4 clips rendered, no OOM (`results.json` `oom: false`, no errors).
- [ ] Peak VRAM (`peak_vram_mib`) under ~23,500 MiB.
- [ ] Timings recorded: `download_s`, `comfy_start_s`, `first_clip_s`, `warm_clip_median_s`, `load_s_estimate`,
      `total_s`; compare with Modal H100 fast (~2 min/clip incl. share of start-up) and price per clip.
- [ ] Pod terminated and `list-pods` empty.

## Open risks

- ComfyUI v0.45.0 node inputs were read from master's source on 20 Nov 2026; a renamed input fails at the first
  clip with a clear ComfyUI error (cheap to fix on the pod, logged in `comfy.log`).
- Community 4090 stock is "Low"; the 48 GB RAM filter narrows it further.
- `requirements.txt` of ComfyUI may pull a different torch; check before rendering (step 4).
- The noise differs from Diffusers for the same seed, so the A/B is a quality judgement, not a pixel diff.
- Producer stop-rule negative fixes are not stored per asset; with CFG 1.0 negatives have no effect anyway.
- If the old runpodctl in the image lacks `pod delete`, the guard falls back to the REST API with the pod's own key
  (see `PodSelfStop`); the coordinator's own `delete-pod` in step 6 is the primary teardown.
