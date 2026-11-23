"""Rent ONE RunPod pod for the Wan 2.2 vs LTX 2.5 bake-off, start the kill-timer, wait until ComfyUI answers."""
import base64, json, os, re, subprocess, sys, time, urllib.request, urllib.error

sys.path.insert(0, r"C:\dev\project-v")
from workers.pod import server as wan   # stdlib-only at import: gives us the exact Wan file list and graph

WORK = r"E:\project-data\outputs\cathedral-wan\work"
STATE = os.environ.get("POD_STATE") or os.path.join(WORK, "pod.json")
env = {}
for line in open(r"C:\dev\project-v\.env", encoding="utf-8"):
    m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"')
KEY, HF = env["RUNPOD_API_KEY"], env["HF_TOKEN"]
import secrets
TOKEN = secrets.token_hex(16)
MAX_HOURS = float(os.environ.get("POD_HOURS", "1.8"))
MAX_PRICE = 1.00   # $/h ceiling (2 h at this price = $3.2, inside the owner's $5)

LTX = "Lightricks/LTX-2.5"
files = {n: list(v) for n, v in wan.FILES.items()}
for sub, path in (("diffusion_models", "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"),
                  ("text_encoders", "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"),
                  ("vae", "vae/ltx-2.5-audio-vae-bf16.safetensors"),
                  ("vae", "vae/ltx-2.5-video-vae-bf16.safetensors")):
    files[os.path.basename(path)] = [LTX, path, sub, 0]

ONLY = os.environ.get("POD_ONLY")
if ONLY == "blender":
    files = {}
if ONLY == "wan":
    files = {n: v for n, v in files.items() if not n.startswith(("ltx", "gemma"))}
if ONLY == "ltx":
    files = {n: v for n, v in files.items() if n.startswith(("ltx", "gemma"))}

SRV = r'''
import http.server, os, subprocess
TOKEN = os.environ["EXEC_TOKEN"]
class H(http.server.SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/x/" + TOKEN:
            self.send_error(403); return
        n = int(self.headers.get("Content-Length", 0)); cmd = self.rfile.read(n).decode()
        try:
            r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=110)
            out = (r.stdout + r.stderr)[-60000:]; code = r.returncode
        except Exception as e:
            out = str(e); code = -1
        b = ("[exit %s]" % code + chr(10) + out).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
os.makedirs("/workspace/pub", exist_ok=True)
for n in ("logs", "out"):
    os.makedirs("/workspace/" + n, exist_ok=True)
    if not os.path.exists("/workspace/pub/" + n): os.symlink("/workspace/" + n, "/workspace/pub/" + n)
os.chdir("/workspace/pub")
http.server.ThreadingHTTPServer(("0.0.0.0", 8189), H).serve_forever()
'''

DL = r'''
import json, os, shutil, sys, time, threading
from huggingface_hub import hf_hub_download
files = json.load(open("/workspace/files.json"))
root = "/workspace/ComfyUI-master/models"
def one(name, v):
    repo, path, sub, size = v
    t = time.time()
    for attempt in (1, 2, 3):
        try:
            p = hf_hub_download(repo, path, local_dir="/workspace/dl", token=os.environ.get("HF_TOKEN"))
            os.makedirs(f"{root}/{sub}", exist_ok=True)
            shutil.move(p, f"{root}/{sub}/{name}")
            if name.startswith("ltx-2.5-22b-distilled-transformer"):
                os.makedirs(f"{root}/checkpoints", exist_ok=True)
                try:
                    os.link(f"{root}/{sub}/{name}", f"{root}/checkpoints/{name}")
                except FileExistsError:
                    pass
            gb = os.path.getsize(f"{root}/{sub}/{name}") / 1e9
            dt = time.time() - t
            print(f"DOWNLOADED {name} {gb:.1f} GB in {dt:.0f}s = {gb*1000/dt:.0f} MB/s", flush=True)
            return
        except Exception as e:
            print(f"retry {attempt} {name}: {type(e).__name__} {str(e)[:150]}", flush=True)
            time.sleep(5)
    print(f"FAILED {name}", flush=True)
order = sorted(files.items(), key=lambda kv: kv[0].startswith("ltx") is False)   # LTX first (tests the gate early)
ths = []
t0 = time.time()
sem = threading.Semaphore(3)
def run(n, v):
    with sem:
        one(n, v)
for n, v in order:
    th = threading.Thread(target=run, args=(n, v)); th.start(); ths.append(th)
for th in ths: th.join()
print(f"ALL DOWNLOADS DONE in {time.time()-t0:.0f}s", flush=True)
'''

BOOT = r'''#!/bin/bash
mkdir -p /workspace/logs /workspace/out && echo "$SRV_B64" | base64 -d > /workspace/srv.py && (nohup python3 /workspace/srv.py >/dev/null 2>&1 &)
exec > >(tee -a /workspace/logs/boot.log) 2>&1
date; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader; free -g | head -2; df -h /workspace | tail -1
cd /workspace; T0=$(date +%s)
echo "$FILES_B64" | base64 -d > /workspace/files.json
echo "$DL_B64" | base64 -d > /workspace/dl.py
curl -sL https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.tar.gz | tar xz
cd ComfyUI-master
grep -viE '^(torch|torchvision|torchaudio)([=<>~ ]|$)' requirements.txt > /workspace/req.txt
pip install -q -r /workspace/req.txt hf_transfer "huggingface_hub>=0.34" 2>&1 | tail -2
echo "DEPS DONE $(( $(date +%s)-T0 ))s"
nohup python main.py --listen 0.0.0.0 --port 8188 --disable-auto-launch --disable-smart-memory > /workspace/logs/comfy.log 2>&1 &
export HF_HUB_ENABLE_HF_TRANSFER=1 HF_HUB_DISABLE_XET=1
python /workspace/dl.py
echo "BOOT COMPLETE $(( $(date +%s)-T0 ))s"
sleep infinity
'''


def gql(query, variables=None):
    req = urllib.request.Request("https://api.runpod.io/graphql?api_key=" + KEY, method="POST",
                                 data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "projectv-cathedral"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"errors": [{"message": "HTTP %s %s" % (e.code, e.read().decode()[:300])}]}


CREATE = "mutation($input: PodFindAndDeployOnDemandInput){ podFindAndDeployOnDemand(input:$input){ id costPerHr desiredStatus machineId } }"
TERMINATE = "mutation($input: PodTerminateInput!){ podTerminate(input:$input) }"


GPUS = ["NVIDIA GeForce RTX 4090"]
pod = None
BOOT_B64 = base64.b64encode(BOOT.encode()).decode()
for cloud in ("COMMUNITY",):
    for g in GPUS:
        inp = {"cloudType": cloud, "gpuCount": 1, "gpuTypeId": g, "name": "projectv-cathedral-" + (ONLY or "both"),
               "imageName": "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04",
               "containerDiskInGb": 90, "volumeInGb": 0, "minVcpuCount": 4, "minMemoryInGb": 48,
               "ports": "8188/http,8189/http,22/tcp",
               "dockerArgs": 'bash -c "echo %s | base64 -d | bash"' % BOOT_B64,
               "env": [{"key": "HF_TOKEN", "value": HF}, {"key": "EXEC_TOKEN", "value": TOKEN},
                       {"key": "SRV_B64", "value": base64.b64encode(SRV.encode()).decode()},
                       {"key": "FILES_B64", "value": base64.b64encode(json.dumps(files).encode()).decode()},
                       {"key": "DL_B64", "value": base64.b64encode(DL.encode()).decode()}]}
        r = gql(CREATE, {"input": inp})
        d = (r.get("data") or {}).get("podFindAndDeployOnDemand")
        if d and d.get("id"):
            price = d.get("costPerHr")
            print(f"RENTED {g} in {cloud}: pod {d['id']} costPerHr={price}", flush=True)
            if price and float(price) > MAX_PRICE:
                print("price above ceiling, terminating", flush=True)
                gql(TERMINATE, {"input": {"podId": d["id"]}})
                continue
            pod = {"id": d["id"], "gpu": g, "cloud": cloud, "costPerHr": price, "created": time.time(), "released": False, "token": TOKEN}
            break
        print(f"  no {g} in {cloud}: {str((r.get('errors') or [{}])[0].get('message'))[:140]}", flush=True)
    if pod:
        break
if not pod:
    sys.exit("could not rent any suitable GPU")
json.dump(pod, open(STATE, "w"))
# Watchdog lease (owner approved 23 Nov 2026): the studio watchdog kills projectv-* pods without a lease after 10 min.
subprocess.Popen([sys.executable, r"C:\\dev\\project-v\\tools\\gpu_lease\\lease.py", "keep", "runpod", pod["id"], "projectv-cathedral-" + (ONLY or "both"), "cathedral wan test",
                  "--state", STATE, "--max-minutes", str(max(20, int(MAX_HOURS * 60) - 10))],
                 creationflags=0x00000008 | 0x08000000, close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.Popen([sys.executable, r"C:\dev\project-v\.claude\worktrees\cathedral-wan-190639\tools\cathedral_wan\killer.py", pod["id"], str(int(MAX_HOURS * 3600)), STATE],
                 creationflags=0x00000008 | 0x00000200, close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("kill-timer started (deadline %.1f h)" % MAX_HOURS, flush=True)


# CUDA gate (23 Nov retry): the first pod had a host where torch could not init CUDA. Check at once; kill the pod if bad.
def _sh(cmd):
    req = urllib.request.Request(f"https://{pod['id']}-8189.proxy.runpod.net/x/{TOKEN}", data=cmd.encode(), headers={"User-Agent": "x"}, method="POST")
    return urllib.request.urlopen(req, timeout=100).read().decode(errors="replace")
_t = time.time(); ok = None
while time.time() - _t < 8 * 60 and ok is None:
    time.sleep(10)
    try:
        out = _sh("python3 -c 'import torch;print(\"CUDAOK\" if torch.cuda.is_available() else \"CUDABAD\")' 2>/dev/null")
        ok = "CUDAOK" in out if ("CUDAOK" in out or "CUDABAD" in out) else None
    except Exception:
        pass
print("CUDA gate:", ok, flush=True)
if ok is not True:
    gql(TERMINATE, {"input": {"podId": pod["id"]}})
    pod["released"] = True; json.dump(pod, open(STATE, "w"))
    sys.exit("CUDA gate failed; pod terminated")

base = f"https://{pod['id']}-8188.proxy.runpod.net"
logu = f"https://{pod['id']}-8189.proxy.runpod.net/boot.log"
t0 = time.time()
seen = 0
while time.time() - t0 < 35 * 60:
    time.sleep(20)
    try:
        txt = urllib.request.urlopen(urllib.request.Request(logu, headers={"User-Agent": "x"}), timeout=15).read().decode(errors="replace")
        lines = txt.splitlines()
        for ln in lines[seen:]:
            if re.search(r"DEPS DONE|DOWNLOADED|FAILED|ALL DOWNLOADS|BOOT COMPLETE|NVIDIA|L40|A40|A6000|Gen|Mem:|overlay|retry", ln):
                print("  pod:", ln[:160], flush=True)
        seen = len(lines)
    except Exception:
        pass
    try:
        urllib.request.urlopen(urllib.request.Request(base + "/system_stats", headers={"User-Agent": "x"}), timeout=15)
        print("COMFYUI UP after %.0fs" % (time.time() - t0), flush=True)
        break
    except Exception:
        pass
else:
    print("ComfyUI not up after 35 min; pod kept for inspection (kill-timer still armed)", flush=True)
    sys.exit(3)
