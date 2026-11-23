"""LTX 2.5 version of the 20 s game cinematic (owner asked 23 Nov 2026: "how LTX 2.5 will do it").
ONE RunPod pod, cap $0.60: rent -> download LTX weights + ESRGAN -> 4 image-to-video clips (704x1280, 24 fps, own audio) from the
approved keyframes -> Real-ESRGAN + Lanczos to 2160x3840 per clip while time allows -> pull -> ALWAYS terminate. Never prints keys."""
import base64, json, os, re, subprocess, sys, time, urllib.request, urllib.error, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from workers.pod import server as pod_server

OUT = r"E:\project-data\outputs\game-cinematic"
LT = OUT + r"\ltx"
WORK = OUT + r"\work"
STATE = WORK + r"\pod_ltx.json"
KILLER = r"C:\dev\project-v\tools\bakeoff\killer.py"
LEASE = r"C:\dev\project-v\tools\gpu_lease\lease.py"
env = {}
for line in open(r"C:\dev\project-v\.env", encoding="utf-8"):
    m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"')
KEY, HF = env["RUNPOD_API_KEY"], env["HF_TOKEN"]
CAP_USD = 0.60   # 0.60 total for the LTX test minus ~0.12 already spent on a stalled China-hosted Vast box and a stray pod
MAX_PRICE = 1.20
H = {"User-Agent": "projectv-gamecine"}
os.makedirs(LT, exist_ok=True)
VAST = os.environ.get("PROVIDER") == "vast"
VAST_STATE = r"E:\project-data\outputs/bakeoff\work\pod_vast.json"
if VAST:
    STATE = VAST_STATE

LTX = "Lightricks/LTX-2.5"
FILES = {
    "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors": [LTX, "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors", "diffusion_models", 0],
    "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors": [LTX, "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors", "text_encoders", 0],
    "ltx-2.5-audio-vae-bf16.safetensors": [LTX, "vae/ltx-2.5-audio-vae-bf16.safetensors", "vae", 0],
    "ltx-2.5-video-vae-bf16.safetensors": [LTX, "vae/ltx-2.5-video-vae-bf16.safetensors", "vae", 0],
}
UNET, TEXT, VAE, AVAE = list(FILES)[0], list(FILES)[1], "ltx-2.5-video-vae-bf16.safetensors", "ltx-2.5-audio-vae-bf16.safetensors"
SIGMAS = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
NEG = "blurry, low quality, jitter, warping, distorted, text, watermark, subtitles, cartoon, people, faces"
SND = " Sound: deep cinematic drone, rushing wind, distant rumbling, crackling embers, low epic score."
SHOTS = [
    ("s1", "s1_wide_101", "Slow cinematic aerial push forward over a ruined sci-fi city skyline at dusk, storm clouds churn and drift, god-rays shift through the glowing sky, embers float, a vast creature glides far in the clouds, smooth steady camera, game trailer." + SND),
    ("s2", "s2_avenue_101", "Smooth steady camera glides forward down a broken avenue, ground mist rolls past, embers drift upward, volumetric light shafts flicker, clouds move in the sky, creature silhouette gently flaps far above, game trailer." + SND + " A distant creature screech."),
    ("s3", "s3_rise_dragon_301", "A colossal dragon creature slowly rises and spreads its leathery wings, glowing underbelly pulses, sun rays flare behind it, clouds swirl, dust and embers drift, slow camera push upward, epic scale, game trailer." + SND + " A deep monstrous roar."),
    ("s4", "s4_overhead_101", "Camera slowly tilts up as the colossal winged creature passes overhead, wings beat slowly, glowing underbelly lights the clouds, embers drift upward, tower edges slide past, epic scale, game trailer." + SND + " Heavy wing beats and a thunderous low roar."),
]


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
            if name.startswith("ltx-2.5-22b-distilled-transformer"):
                os.makedirs(f"{root}/checkpoints", exist_ok=True)
                try: os.link(f"{root}/{sub}/{name}", f"{root}/checkpoints/{name}")
                except FileExistsError: pass
            print(f"DOWNLOADED {name} in {time.time()-t:.0f}s", flush=True); return
        except Exception as e:
            print(f"retry {a} {name}: {type(e).__name__} {str(e)[:120]}", flush=True); time.sleep(5)
    print(f"FAILED {name}", flush=True)
ths = [threading.Thread(target=one, args=(n, v)) for n, v in files.items()]
[t.start() for t in ths]; [t.join() for t in ths]
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
mkdir -p models/upscale_models && wget -q -O models/upscale_models/RealESRGAN_x4plus.pth https://huggingface.co/lllyasviel/Annotators/resolve/main/RealESRGAN_x4plus.pth && echo "ESRGAN DONE"
grep -viE '^(torch|torchvision|torchaudio)([=<>~ ]|$)' requirements.txt > /workspace/req.txt
pip install -q -r /workspace/req.txt 2>&1 | tail -2
echo "DEPS DONE $(( $(date +%s)-T0 ))s"
(while true; do python main.py --listen 0.0.0.0 --port 8188 --disable-auto-launch --disable-smart-memory >> /workspace/logs/comfy.log 2>&1; echo "COMFY EXITED, restarting" >> /workspace/logs/comfy.log; sleep 3; done) &
wait
echo "BOOT COMPLETE $(( $(date +%s)-T0 ))s"
sleep infinity
'''


def say(m):
    print(time.strftime("%H:%M:%S ") + m, flush=True)


def vcall(method, path, body=None):
    req = urllib.request.Request("https://console.vast.ai/api/v0" + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + VKEY, "Content-Type": "application/json", **H}, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"_http": e.code, "_b": e.read().decode()[:300]}


VKEY = None


def terminate(pid):
    for _ in range(5):
        if VAST:
            r = vcall("DELETE", "/instances/%s/" % pid)
            if r.get("success") or r.get("_http") == 404:
                return True
        elif not gql(TERMINATE, {"input": {"podId": pid}}).get("errors"):
            return True
        time.sleep(5)
    return False


def rent_vast():
    global VKEY
    sys.path.insert(0, r"E:\project-data\outputs/bakeoff\work")
    import vastauth
    VKEY = vastauth.login()
    q = {"limit": 40, "type": "on-demand", "verified": {"eq": True}, "rentable": {"eq": True}, "gpu_ram": {"gte": 78000}, "disk_space": {"gte": 150},
         "direct_port_count": {"gte": 4}, "inet_down": {"gte": 800}, "reliability2": {"gte": 0.97}, "dph_total": {"lte": MAX_PRICE},
         "cpu_ram": {"gte": 48000}, "num_gpus": {"eq": 1}, "order": [["dph_total", "asc"]]}
    offers = [o for o in vcall("POST", "/bundles/", q).get("offers", []) if "GB10" not in o["gpu_name"] and "CMP" not in o["gpu_name"] and "RTX 8000" not in o["gpu_name"] and not str(o.get("geolocation", "")).endswith("CN")]
    for o in offers[:5]:
        say(f"offer {o['id']} {o['gpu_name']} ${o['dph_total']:.3f}/h {o.get('geolocation')}")
    env_vars = {"-p 8188:8188": "1", "-p 8189:8189": "1", "HF_TOKEN": HF,
                "SRV_B64": base64.b64encode(SRV.encode()).decode(), "FILES_B64": base64.b64encode(json.dumps(FILES).encode()).decode(),
                "DL_B64": base64.b64encode(DL.encode()).decode()}
    for o in offers[:4]:
        r = vcall("PUT", "/asks/%s/" % o["id"], {"client_id": "me", "image": "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04", "disk": 150,
                  "runtype": "ssh", "env": env_vars, "onstart": BOOT, "label": "projectv-gamecine-ltx"})
        if r.get("success") and r.get("new_contract"):
            say(f"RENTED vast {o['gpu_name']} {r['new_contract']} ${o['dph_total']:.3f}/h")
            return {"id": str(r["new_contract"]), "gpu": o["gpu_name"], "cloud": "vast", "costPerHr": float(o["dph_total"]), "created": time.time(),
                    "released": False, "session_key": VKEY}
        say(f"could not rent {o['id']}: {str(r)[:150]}")
    return None


def main():
    pod = None
    for cloud in (() if VAST else ("SECURE", "COMMUNITY")):
        for g in ("NVIDIA A40", "NVIDIA RTX A6000", "NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation", "NVIDIA L40"):
            inp = {"cloudType": cloud, "gpuCount": 1, "gpuTypeId": g, "name": "projectv-gamecine-ltx",
                   "imageName": "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04",
                   "containerDiskInGb": 100, "volumeInGb": 0, "minVcpuCount": 4, "minMemoryInGb": 64,
                   "ports": "8188/http,8189/http",
                   "dockerArgs": 'bash -c "echo %s | base64 -d | bash"' % base64.b64encode(BOOT.encode()).decode(),
                   "env": [{"key": "HF_TOKEN", "value": HF},
                           {"key": "SRV_B64", "value": base64.b64encode(SRV.encode()).decode()},
                           {"key": "FILES_B64", "value": base64.b64encode(json.dumps(FILES).encode()).decode()},
                           {"key": "DL_B64", "value": base64.b64encode(DL.encode()).decode()}]}
            r = gql(CREATE, {"input": inp})
            d = (r.get("data") or {}).get("podFindAndDeployOnDemand")
            if d and d.get("id"):
                price = float(d.get("costPerHr") or 0)
                say(f"RENTED {g} {cloud} pod {d['id']} ${price}/h")
                if price > MAX_PRICE or price <= 0:
                    say("price outside ceiling, terminating"); terminate(d["id"]); continue
                pod = {"id": d["id"], "gpu": g, "cloud": cloud, "costPerHr": price, "created": time.time(), "released": False}
                break
            say(f"no {g} in {cloud}: {str((r.get('errors') or [{}])[0].get('message'))[:100]}")
        if pod:
            break
    if os.environ.get("RESUME"):
        pod = json.load(open(STATE))
    if not pod and VAST:
        pod = rent_vast()
    if not pod:
        sys.exit("no GPU on RunPod")
    price = pod["costPerHr"]
    limit_min = min(70, int(CAP_USD / price * 60) - 2)   # hard time limit so spend stays under the cap
    pod["limit_min"] = limit_min
    json.dump(pod, open(STATE, "w"))
    if not os.environ.get("RESUME"):
      subprocess.Popen([sys.executable, LEASE, "keep", "vast" if VAST else "runpod", pod["id"], "projectv-gamecine-ltx", "game cinematic ltx test",
                      "--state", STATE, "--max-minutes", str(limit_min + 4)], creationflags=0x08000000 | 0x00000008, close_fds=True,
                     stdout=open(WORK + r"\lease_ltx.log", "w"), stderr=subprocess.STDOUT)
    subprocess.Popen([sys.executable, (r"E:\project-data\outputs/bakeoff\work\killer_vast.py" if VAST else KILLER), pod["id"], str(limit_min * 60)] + ([] if VAST else [STATE]),
                     creationflags=0x00000008 | 0x00000200, close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    say(f"lease + kill-timer started: limit {limit_min} min at ${price}/h = ${price*limit_min/60:.2f}")
    pid = pod["id"]; b8188 = f"https://{pid}-8188.proxy.runpod.net"; b8189 = f"https://{pid}-8189.proxy.runpod.net"
    if VAST:
        ok_ports = False
        for _ in range(90):
            time.sleep(20)
            r = vcall("GET", "/instances/%s/" % pid); i = r.get("instances", r)
            i = (i[0] if i else {}) if isinstance(i, list) else i
            ports = i.get("ports") or {}
            if i.get("actual_status") == "running" and ports.get("8188/tcp") and ports.get("8189/tcp"):
                ip = i.get("public_ipaddr")
                b8188 = "http://%s:%s" % (ip, ports["8188/tcp"][0]["HostPort"]); b8189 = "http://%s:%s" % (ip, ports["8189/tcp"][0]["HostPort"])
                ok_ports = True; break
        if not ok_ports:
            say("vast instance never became reachable"); terminate(pid); pod["released"] = True; json.dump(pod, open(STATE, "w")); return
        say("vast instance reachable")
    t_end = pod["created"] + limit_min * 60

    def get(path, raw=False, timeout=60):
        b = urllib.request.urlopen(urllib.request.Request(b8188 + path, headers=H), timeout=timeout).read()
        return b if raw else json.loads(b)

    def post(path, body, timeout=120):
        return json.loads(urllib.request.urlopen(urllib.request.Request(b8188 + path, data=json.dumps(body).encode(), headers={**H, "Content-Type": "application/json"}), timeout=timeout).read())

    def upload(path, name, mime="image/png"):
        bd = uuid.uuid4().hex
        body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: {mime}\r\n\r\n").encode() + open(path, "rb").read() + \
               (f"\r\n--{bd}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{bd}--\r\n").encode()
        urllib.request.urlopen(urllib.request.Request(b8188 + "/upload/image", data=body, headers={**H, "Content-Type": "multipart/form-data; boundary=" + bd}), timeout=300).read()

    def run(graph, wait_s):
        pid_ = post("/prompt", {"prompt": graph})["prompt_id"]
        t0 = time.time()
        while time.time() - t0 < wait_s and time.time() < t_end - 30:
            time.sleep(5)
            h = get("/history/" + pid_)
            if pid_ in h:
                st = h[pid_].get("status", {})
                if st.get("status_str") == "error":
                    raise RuntimeError("comfy error: " + " | ".join(str(m[1].get("exception_message", ""))[:600] + " @ " + str(m[1].get("node_type")) for m in st.get("messages", []) if m[0] == "execution_error"))
                if st.get("completed"):
                    return h[pid_]["outputs"], time.time() - t0
        raise TimeoutError("job timeout / out of time")

    def pull(outs, dest):
        for o in outs.values():
            for k in ("images", "videos", "gifs"):
                for it in (o.get(k) or []):
                    if it["filename"].endswith(".mp4"):
                        q = "filename=%s&subfolder=%s&type=%s" % (it["filename"], it.get("subfolder", ""), it.get("type", "output"))
                        open(dest, "wb").write(get("/view?" + q, raw=True, timeout=900)); return os.path.getsize(dest)
        raise RuntimeError("no mp4 in outputs")

    try:
        seen = 0; ready = False; t0 = time.time()
        while time.time() - t0 < 22 * 60:
            time.sleep(15)
            try:
                lines = urllib.request.urlopen(urllib.request.Request(b8189 + "/logs/boot.log", headers=H), timeout=15).read().decode(errors="replace").splitlines()
                for ln in lines[seen:]:
                    if re.search(r"DOWNLOADED|DEPS|ESRGAN|FAILED|CUDA|NVIDIA|BOOT|ALL|retry", ln):
                        say("pod: " + ln[:140])
                seen = len(lines); txt = "\n".join(lines)
                if "CUDA False" in txt or "FAILED" in txt:
                    say("pod problem, abort"); return
                if "ALL DOWNLOADS DONE" in txt and "DEPS DONE" in txt:
                    time.sleep(20)
                    ready = True; break
            except Exception:
                pass
        if not ready:
            say("not ready in 22 min, abort"); return
        say(f"ready after {time.time()-t0:.0f}s")
        up = False
        for _ in range(30):
            try:
                get("/system_stats", timeout=15); up = True; break
            except Exception:
                time.sleep(10)
        if not up:
            try:
                tail = urllib.request.urlopen(urllib.request.Request(b8189 + "/logs/comfy.log", headers=H), timeout=20).read().decode(errors="replace").splitlines()[-40:]
                say("ComfyUI unreachable on 8188; comfy.log tail: " + " || ".join(tail))
            except Exception as e2:
                say(f"ComfyUI unreachable and comfy.log unreadable: {e2}")
            return
        say("ComfyUI answers")
        for i, (tag, kf, prompt) in enumerate(SHOTS):
            dest = LT + rf"\ltx_{tag}.mp4"
            if os.path.exists(dest):
                continue
            upload(OUT + rf"\keyframes\raw\{kf}.png", f"gcl_{tag}.png")
            g = {
                "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
                "2": {"class_type": "LTXAVTextEncoderLoader", "inputs": {"text_encoder": TEXT, "ckpt_name": UNET, "device": "default"}},
                "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
                "4": {"class_type": "VAELoader", "inputs": {"vae_name": AVAE}},
                "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
                "6": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["2", 0]}},
                "7": {"class_type": "LTXVConditioning", "inputs": {"positive": ["5", 0], "negative": ["6", 0], "frame_rate": 24.0}},
                "8": {"class_type": "LoadImage", "inputs": {"image": f"gcl_{tag}.png"}},
                "8b": {"class_type": "ImageScale", "inputs": {"image": ["8", 0], "upscale_method": "lanczos", "width": 704, "height": 1280, "crop": "center"}},
                "9": {"class_type": "LTXVPreprocess", "inputs": {"image": ["8b", 0], "img_compression": 35}},
                "10": {"class_type": "LTXVImgToVideo", "inputs": {"positive": ["7", 0], "negative": ["7", 1], "vae": ["3", 0], "image": ["9", 0],
                                                                  "width": 704, "height": 1280, "length": 121, "batch_size": 1, "strength": 1.0}},
                "11": {"class_type": "LTXVEmptyLatentAudio", "inputs": {"frames_number": 121, "frame_rate": 24, "batch_size": 1, "audio_vae": ["4", 0]}},
                "12": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["10", 2], "audio_latent": ["11", 0]}},
                "13": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
                "14": {"class_type": "ManualSigmas", "inputs": {"sigmas": SIGMAS}},
                "15": {"class_type": "RandomNoise", "inputs": {"noise_seed": 500 + i}},
                "16": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["10", 0], "negative": ["10", 1], "cfg": 1.0}},
                "17": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["15", 0], "guider": ["16", 0], "sampler": ["13", 0], "sigmas": ["14", 0], "latent_image": ["12", 0]}},
                "18": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["17", 0]}},
                "19": {"class_type": "VAEDecode", "inputs": {"samples": ["18", 0], "vae": ["3", 0]}},
                "20": {"class_type": "LTXVAudioVAEDecode", "inputs": {"samples": ["18", 1], "audio_vae": ["4", 0]}},
                "cv": {"class_type": "CreateVideo", "inputs": {"images": ["19", 0], "fps": 24.0, "audio": ["20", 0]}},
                "sv": {"class_type": "SaveVideo", "inputs": {"video": ["cv", 0], "filename_prefix": f"video/gcl_{tag}", "format": "mp4", "codec": "h264"}},
            }
            outs, dt = run(g, 1200)
            say(f"{tag} generated in {dt:.0f}s, {pull(outs, dest)//1024} KB; {(t_end-time.time())/60:.1f} min left")
        # upscale while time allows
        for tag, _, _ in SHOTS:
            dest = LT + rf"\up4k_ltx_{tag}.mp4"
            if os.path.exists(dest):
                continue
            if time.time() > t_end - 11 * 60:
                say(f"not enough time left for upscale of {tag}; skipping the rest"); break
            upload(LT + rf"\ltx_{tag}.mp4", f"gcl_{tag}.mp4", "video/mp4")
            g = {
                "1": {"class_type": "LoadVideo", "inputs": {"file": f"gcl_{tag}.mp4"}},
                "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
                "3": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "RealESRGAN_x4plus.pth"}},
                "4": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["3", 0], "image": ["2", 0]}},
                "5": {"class_type": "ImageScale", "inputs": {"image": ["4", 0], "upscale_method": "lanczos", "width": 2160, "height": 3840, "crop": "disabled"}},
                "6": {"class_type": "CreateVideo", "inputs": {"images": ["5", 0], "fps": ["2", 2], "audio": ["2", 1]}},
                "7": {"class_type": "SaveVideo", "inputs": {"video": ["6", 0], "filename_prefix": f"video/up4k_ltx_{tag}", "format": "mp4", "codec": "h264"}},
            }
            outs, dt = run(g, 1500)
            say(f"{tag} upscaled in {dt:.0f}s, {pull(outs, dest)//1024} KB; {(t_end-time.time())/60:.1f} min left")
        say("ALL DONE")
    except Exception as e:
        say(f"ERROR {type(e).__name__} {str(e)[:1500]}")
    finally:
        ok = terminate(pid)
        pod["released"] = True; json.dump(pod, open(STATE, "w"))
        mins = (time.time() - pod["created"]) / 60
        say(f"TERMINATED ok={ok}; minutes {mins:.1f}; est cost ${price*mins/60:.2f}")


main()
