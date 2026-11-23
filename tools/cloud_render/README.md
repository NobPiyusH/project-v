# cloud_render: crash-proof Blender rendering on RunPod

`cloud_render.py` rents one GPU pod, installs Blender 5.3, uploads the scene, renders in short chunks, pulls each frame to the
laptop the moment it exists, and terminates the pod at the end. If the pod vanishes (seen all week after ~10-20 min), it detects
that (API says gone, or unreachable for 5 min), rents a new one and resumes from the first missing frame. Every pod also gets an
independent detached `killer.py` timer. Frames only exist locally under their final name once fully downloaded and PNG-checked.

    python tools/cloud_render/cloud_render.py --plan tools/cloud_render/plan_bench_4k.json --max-minutes 28
    python tools/cloud_render/cloud_render.py --scene a --frames 1-24 --chunk 6 --width 2160 --height 3840 --out E:/.../renders/a

Free logic test (no money): `--backend local --simulate-death-after 1` (renders previews on the laptop and "kills" the pod once).
Cost log: `E:\project-data\blender\milky-seas\cost_log.md`. Key: `RUNPOD_API_KEY` in `.env`, never printed.
