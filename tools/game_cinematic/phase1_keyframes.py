"""Game-cinematic 20 s test, step 1: rent ONE RunPod 4090 (cap $0.70 / 75 min for the whole test), start lease + kill-timer,
make FLUX.2 klein keyframes while the Wan weights download, pull the keyframes, then LEAVE THE POD ON (state in pod.json).
Step 2 (clips) is a separate script. On any failure the pod is terminated. Never prints keys."""
import base64, json, os, re, subprocess, sys, time, urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from workers.pod import server as pod_server

OUT = r"E:\project-data\outputs\game-cinematic"
WORK = OUT + r"\work"
STATE = WORK + r"\pod.json"
KILLER = r"C:\dev\project-v\tools\bakeoff\killer.py"
LEASE = r"C:\dev\project-v\tools\gpu_lease\lease.py"
env = {}
for line in open(r"C:\dev\project-v\.env", encoding="utf-8"):
    m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"')
KEY, HF = env["RUNPOD_API_KEY"], env["HF_TOKEN"]
MAX_PRICE = 0.90
DEADLINE_MIN = 75
H = {"User-Agent": "projectv-gamecine"}

STYLE = ("Unreal Engine 5 game cinematic trailer still, Lumen global illumination, ray-traced reflections, volumetric light, "
         "deep contrast, razor sharp detail, subtle film grain, anamorphic lens, palette of burnt orange dusk and teal storm clouds, "
         "no people, no faces, no text")
WORLD = "a ruined sci-fi city at dusk, collapsed megastructure towers, a colossal winged leviathan creature in the sky"
SHOTS = {
    "s1_wide": "extreme wide establishing shot from high above a ruined sci-fi city skyline at dusk, broken glass towers, glowing storm sky "
               "of orange and teal clouds, tiny distant silhouette of a vast creature far away in the clouds, god-rays, ",
    "s2_avenue": "low camera pushing down a broken avenue between leaning ruined skyscrapers, drifting embers and ground mist, cracked asphalt "
                 "reflecting orange sky, shafts of volumetric light, enormous creature silhouette faint in the sky above the street, ",
    "s3_rise": "the silhouette of a colossal winged leviathan rising behind ruined towers, enormous scale, backlit by a burning orange sun, "
               "crepuscular god-rays through storm clouds, dark skyline in foreground, ",
    "s4_overhead": "looking steeply up from a ruined street as a colossal winged leviathan passes overhead blotting out the sky, glowing underbelly lit "
                   "orange, tower edges framing the shot, embers drifting, volumetric clouds, ",
}
JOBS = [(f"{k}_{seed}", v + WORLD + ", " + STYLE, seed) for k, v in SHOTS.items() for seed in (101, 202)]


def gql(query, variables=None):
    req = urllib.request.Request("https://api.runpod.io/graphql?api_key=" + KEY, method="POST",
                                 data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                 headers={"Content-Type": "application/json", **H})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=90).read())
    except urllib.error.HTTPError as e:
        return {"errors": [{"message": "HTTP %s %s" % (e.code, e.read().decode()[:300])}]}


TERMINATE = "mutation($input: PodTerminateInput!){ podTerminate(input:$input) }"
CREATE = "mutation($input: PodFindAndDeployOnDemandInput){ podFindAndDeployOnDemand(input:$input){ id costPerHr desiredStatus machineId } }"

SRV = r'''
import http.server, os
os.makedirs("/workspace/pub", exist_ok=True); os.chdir("/workspace/pub")
if not os.path.exists("logs"): os.symlink("/workspace/logs", "logs")
class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("0.0.0.0", 8189), Q).serve_forever()
'''
DL = r'''
import json, os, shutil, time, threading
from huggingface_hub import hf_hub_download
files = json.load(open("/workspace/files.json")); root = "/workspace/ComfyUI-master/models"
def one(name, v):
    repo, path, sub, size = v; t = time.time()
    for a in (1, 2, 3):
        try:
            p = hf_hub_download(repo, path, local_dir="/workspace/dl", token=os.environ.get("HF_TOKEN"))
            os.makedirs(f"{root}/{sub}", exist_ok=True); shutil.move(p, f"{root}/{sub}/{name}")
            print(f"DOWNLOADED {name} in {time.time()-t:.0f}s", flush=True); return
        except Exception as e:
            print(f"retry {a} {name}: {type(e).__name__} {str(e)[:120]}", flush=True); time.sleep(5)
    print(f"FAILED {name}", flush=True)
def run(group):
    ths = [threading.Thread(target=one, args=(n, files[n])) for n in group]
    [t.start() for t in ths]; [t.join() for t in ths]
flux = [n for n in files if "flux" in n or n.startswith("qwen")]
run(flux); print("FLUX FILES DONE", flush=True)
run([n for n in files if n not in flux])
print("ALL DOWNLOADS DONE", flush=True)
'''
BOOT = r'''#!/bin/bash
mkdir -p /workspace/logs && echo "$SRV_B64" | base64 -d > /workspace/srv.py && (nohup python3 /workspace/srv.py >/dev/null 2>&1 &)
exec > >(tee -a /workspace/logs/boot.log) 2>&1
date; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader; python3 -c "import torch;print('CUDA',torch.cuda.is_available())"
cd /workspace; T0=$(date +%s)
echo "$FILES_B64" | base64 -d > files.json; echo "$DL_B64" | base64 -d > dl.py
export HF_HUB_ENABLE_HF_TRANSFER=1 HF_HUB_DISABLE_XET=1
pip install -q hf_transfer "huggingface_hub>=0.34" 2>&1 | tail -1
(python dl.py 2>&1) &
curl -sL https://github.com/comfyanonymous/ComfyUI/archive/refs/heads/master.tar.gz | tar xz
cd ComfyUI-master
grep -viE '^(torch|torchvision|torchaudio)([=<>~ ]|$)' requirements.txt > /workspace/req.txt
pip install -q -r /workspace/req.txt 2>&1 | tail -2
echo "DEPS DONE $(( $(date +%s)-T0 ))s"
nohup python main.py --listen 0.0.0.0 --port 8188 --disable-auto-launch --disable-smart-memory > /workspace/logs/comfy.log 2>&1 &
wait
echo "BOOT COMPLETE $(( $(date +%s)-T0 ))s"
sleep infinity
'''


def say(m):
    print(time.strftime("%H:%M:%S ") + m, flush=True)


def terminate(pid):
    for _ in range(5):
        if not gql(TERMINATE, {"input": {"podId": pid}}).get("errors"):
            return True
        time.sleep(5)
    return False


def main():
    files = {n: list(v) for n, v in {**pod_server.FLUX_FILES, **pod_server.FILES}.items()}
    pod = None
    for cloud in ("SECURE", "COMMUNITY"):
        for g in ("NVIDIA GeForce RTX 4090", "NVIDIA GeForce RTX 3090", "NVIDIA RTX A5000", "NVIDIA RTX A6000", "NVIDIA A40", "NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation"):
            inp = {"cloudType": cloud, "gpuCount": 1, "gpuTypeId": g, "name": "projectv-gamecine",
                   "imageName": "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04",
                   "containerDiskInGb": 130, "volumeInGb": 0, "minVcpuCount": 4, "minMemoryInGb": 48,
                   "ports": "8188/http,8189/http",
                   "dockerArgs": 'bash -c "echo %s | base64 -d | bash"' % base64.b64encode(BOOT.encode()).decode(),
                   "env": [{"key": "HF_TOKEN", "value": HF},
                           {"key": "SRV_B64", "value": base64.b64encode(SRV.encode()).decode()},
                           {"key": "FILES_B64", "value": base64.b64encode(json.dumps(files).encode()).decode()},
                           {"key": "DL_B64", "value": base64.b64encode(DL.encode()).decode()}]}
            r = gql(CREATE, {"input": inp})
            d = (r.get("data") or {}).get("podFindAndDeployOnDemand")
            if d and d.get("id"):
                price = float(d.get("costPerHr") or 0)
                say(f"RENTED {g} {cloud} pod {d['id']} ${price}/h")
                if price > MAX_PRICE:
                    say("above ceiling, terminating"); terminate(d["id"]); continue
                pod = {"id": d["id"], "gpu": g, "cloud": cloud, "costPerHr": price, "created": time.time(), "released": False}
                break
            say(f"no {g} in {cloud}: {str((r.get('errors') or [{}])[0].get('message'))[:120]}")
        if pod:
            break
    if not pod:
        sys.exit("no GPU on RunPod")
    json.dump(pod, open(STATE, "w"))
    subprocess.Popen([sys.executable, LEASE, "keep", "runpod", pod["id"], "projectv-gamecine", "game cinematic test",
                      "--state", STATE, "--max-minutes", str(DEADLINE_MIN + 5)], creationflags=0x08000000 | 0x00000008, close_fds=True,
                     stdout=open(WORK + r"\lease.log", "w"), stderr=subprocess.STDOUT)
    subprocess.Popen([sys.executable, KILLER, pod["id"], str(DEADLINE_MIN * 60), STATE],
                     creationflags=0x00000008 | 0x00000200, close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    say(f"lease + kill-timer ({DEADLINE_MIN} min) started")
    pid = pod["id"]; b8188 = f"https://{pid}-8188.proxy.runpod.net"; b8189 = f"https://{pid}-8189.proxy.runpod.net"
    t0 = time.time(); ok = False
    try:
        seen = 0; ready = False
        while time.time() - t0 < 20 * 60:
            time.sleep(15)
            try:
                lines = urllib.request.urlopen(urllib.request.Request(b8189 + "/logs/boot.log", headers=H), timeout=15).read().decode(errors="replace").splitlines()
                for ln in lines[seen:]:
                    say("pod: " + ln[:140])
                seen = len(lines)
                txt = "\n".join(lines)
                if "CUDA False" in txt:
                    say("CUDA broken on this host"); break
                if "FAILED" in txt:
                    say("download failed"); break
                if "FLUX FILES DONE" in txt and "DEPS DONE" in txt:
                    time.sleep(20); ready = True; break
            except Exception:
                pass
        if not ready:
            say("not ready in time"); return
        say(f"flux ready after {time.time()-t0:.0f}s")
        for name, prompt, seed in JOBS:
            g = pod_server.flux_graph({"prompt": prompt, "width": 864, "height": 1536, "seed": seed}, name)
            req = urllib.request.Request(b8188 + "/prompt", data=json.dumps({"prompt": g}).encode(), headers={**H, "Content-Type": "application/json"})
            pid_ = json.loads(urllib.request.urlopen(req, timeout=60).read())["prompt_id"]
            for _ in range(200):
                time.sleep(3)
                h = json.loads(urllib.request.urlopen(urllib.request.Request(b8188 + "/history/" + pid_, headers=H), timeout=30).read())
                if pid_ in h and h[pid_].get("outputs"):
                    im = h[pid_]["outputs"]["13"]["images"][0]
                    data = urllib.request.urlopen(urllib.request.Request(
                        b8188 + "/view?filename=%s&subfolder=%s&type=%s" % (im["filename"], im["subfolder"], im["type"]), headers=H), timeout=120).read()
                    open(OUT + r"\keyframes\raw" + "\\" + name + ".png", "wb").write(data)
                    say(f"saved {name} ({len(data)//1024} KB)"); break
            else:
                say(f"timeout {name}"); return
        ok = True
        say(f"KEYFRAMES DONE, pod {pid} left ON; minutes so far {(time.time()-pod['created'])/60:.1f}")
    except Exception as e:
        say(f"ERROR {type(e).__name__} {str(e)[:200]}")
    finally:
        if not ok:
            t = terminate(pid); pod["released"] = True; json.dump(pod, open(STATE, "w"))
            say(f"FAILED -> terminate sent ok={t}")


main()
