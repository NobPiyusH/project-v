"""Boot scripts for a Wan 2.2 + SVI + Real-ESRGAN pod (RunPod pytorch image, no network volume).

Lessons baked in (24 Nov 2026, measured):
  * the boot script must NOT end with a bare `wait`: ComfyUI never exits, so the "BOOT COMPLETE" marker would never print
    (cost a 25-minute pod). It waits for the download process only.
  * readiness = BOOT COMPLETE in the log AND ComfyUI answering /object_info for the SVI node (the orchestrator checks both).
  * the boot takes about 7 minutes (weights 41 GB at ~100 MB/s); a pod without CUDA is a bad host: terminate and rent again.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from . import groups

# Pinned (25 Nov 2026): the commit of lllyasviel/Annotators in groups.REVISIONS, not /resolve/main/ (a floating file).
ESRGAN_URL = f"https://huggingface.co/{groups.ESRGAN[0]}/resolve/{groups.REVISIONS[groups.ESRGAN[0]]}/{groups.ESRGAN[1]}"
ESRGAN_SHA256 = groups.SHA256[groups.ESRGAN[1]]
COMFY_TAG = "v0.45.0"  # the same tag workers/pod/server.py pins; the boot fetched the floating master branch before
COMFY_URL = f"https://github.com/comfyanonymous/ComfyUI/archive/refs/tags/{COMFY_TAG}.tar.gz"
READY_MARKERS = ("BOOT COMPLETE",)
BAD_MARKERS = ("CUDA False", "FAILED")

SRV = """
import http.server, os
os.makedirs("/workspace/pub", exist_ok=True); os.chdir("/workspace/pub")
if not os.path.exists("logs"): os.symlink("/workspace/logs", "logs")
class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("0.0.0.0", 8189), Q).serve_forever()
"""

DL = """
import json, os, shutil, time, threading
from huggingface_hub import hf_hub_download
import sys; sys.path.insert(0, "/workspace"); import verify
files = json.load(open("/workspace/files.json")); pins = json.load(open("/workspace/pins.json")); root = "/workspace/ComfyUI-master/models"
def one(name, v):
    repo, path, sub, size = v; t = time.time(); pin = pins.get(name)
    if not pin: print(f"UNPINNED {name}: downloading without a sha256 check", flush=True)
    for a in (1, 2, 3):
        try:
            p = hf_hub_download(repo, path, local_dir="/workspace/dl", token=os.environ.get("HF_TOKEN"), revision=pin["revision"] if pin else None)
            if pin:
                ok, why = verify.verify_file(p, pin["sha256"], size)
                if not ok:
                    os.remove(p); print(f"SHA MISMATCH {name}: {why}", flush=True); raise ValueError("sha256 mismatch")
                print(f"SHA OK {name}", flush=True)
            os.makedirs(f"{root}/{sub}", exist_ok=True); shutil.move(p, f"{root}/{sub}/{name}")
            print(f"DOWNLOADED {name} in {time.time()-t:.0f}s", flush=True); return
        except Exception as e:
            print(f"retry {a} {name}: {type(e).__name__} {str(e)[:120]}", flush=True); time.sleep(5)
    print(f"FAILED {name}", flush=True)
ths = [threading.Thread(target=one, args=(n, v)) for n, v in files.items()]
[t.start() for t in ths]; [t.join() for t in ths]
print("ALL DOWNLOADS DONE", flush=True)
"""


def boot_script(esrgan: bool = True) -> str:
    esr = (
        (
            f"mkdir -p models/upscale_models && wget -q -O models/upscale_models/RealESRGAN_x4plus.pth {ESRGAN_URL} "
            f'&& echo "{ESRGAN_SHA256}  models/upscale_models/RealESRGAN_x4plus.pth" | sha256sum -c - '
            '&& echo "ESRGAN DONE" || echo "FAILED ESRGAN sha256"\n'
        )
        if esrgan
        else ""
    )
    return f"""#!/bin/bash
mkdir -p /workspace/logs && echo "$SRV_B64" | base64 -d > /workspace/srv.py && (nohup python3 /workspace/srv.py >/dev/null 2>&1 &)
exec > >(tee -a /workspace/logs/boot.log) 2>&1
date; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader; python3 -c "import torch;print('CUDA',torch.cuda.is_available())"
cd /workspace; T0=$(date +%s)
echo "$FILES_B64" | base64 -d > files.json; echo "$PINS_B64" | base64 -d > pins.json; echo "$DL_B64" | base64 -d > dl.py; echo "$VERIFY_B64" | base64 -d > verify.py
export HF_HUB_ENABLE_HF_TRANSFER=1 HF_HUB_DISABLE_XET=1
pip install -q hf_transfer "huggingface_hub>=0.34" 2>&1 | tail -1
(python dl.py 2>&1) &
DLPID=$!
curl -sL "$COMFY_URL" | tar xz && mv ComfyUI-* ComfyUI-master
cd ComfyUI-master
grep -viE '^(torch|torchvision|torchaudio)([=<>~ ]|$)' requirements.txt > /workspace/req.txt
pip install -q -r /workspace/req.txt 2>&1 | tail -2
mkdir -p custom_nodes/ComfyUI-KJNodes && curl -sL "$KJ_URL" | tar xz --strip-components=1 -C custom_nodes/ComfyUI-KJNodes
grep -viE '^(torch|torchvision|torchaudio)([=<>~ ]|$)' custom_nodes/ComfyUI-KJNodes/requirements.txt > /workspace/kjreq.txt
pip install -q -r /workspace/kjreq.txt 2>&1 | tail -2
{esr}echo "DEPS DONE $(( $(date +%s)-T0 ))s"
nohup python main.py --listen 0.0.0.0 --port 8188 --disable-auto-launch --disable-smart-memory > /workspace/logs/comfy.log 2>&1 &
wait $DLPID
echo "BOOT COMPLETE $(( $(date +%s)-T0 ))s"
sleep infinity
"""


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def pod_env(hf_token: str, kj_url: str, files: dict) -> list[dict]:
    """The env entries the boot script reads. `files` maps name -> [repo, path, subfolder, size]."""
    return [
        {"key": "HF_TOKEN", "value": hf_token},
        {"key": "KJ_URL", "value": kj_url},
        {"key": "COMFY_URL", "value": COMFY_URL},
        {"key": "PINS_B64", "value": b64(json.dumps(groups.pins_for(files)))},
        {"key": "VERIFY_B64", "value": b64((Path(__file__).parent / "verify.py").read_text(encoding="utf-8"))},
        {"key": "SRV_B64", "value": b64(SRV)},
        {"key": "FILES_B64", "value": b64(json.dumps(files))},
        {"key": "DL_B64", "value": b64(DL)},
    ]


def docker_args(esrgan: bool = True) -> str:
    return 'bash -c "echo %s | base64 -d | bash"' % b64(boot_script(esrgan))
