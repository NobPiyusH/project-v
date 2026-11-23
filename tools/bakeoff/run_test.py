"""Bake-off runner. usage: run_test.py wan|ltx [--shots 0,3] [--keep]  (pod must be up; see pod.json)"""
import json, os, sys, time, uuid, urllib.request, urllib.error
sys.path.insert(0, r"E:\project-data\outputs\bakeoff\work")
sys.path.insert(0, r"C:\dev\project-v")
from cu import *
from workers.pod import server as wan

OUT = r"E:\project-data\outputs\bakeoff"
TAG = os.environ.get("BAKE_TAG", "")
SHOTS = json.load(open(os.path.join(OUT, "inputs", os.environ.get("BAKE_SHOTS", "shots.json"))))
NEG = wan.NEGATIVE


def upload(path):
    name = "bake_" + os.path.basename(path)
    bound = uuid.uuid4().hex
    body = (f"--{bound}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + open(path, "rb").read() + (f"\r\n--{bound}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{bound}--\r\n").encode()
    req = urllib.request.Request(BASE + "/upload/image", data=body, headers={**H, "Content-Type": "multipart/form-data; boundary=" + bound}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=120).read())["name"]


def wait_downloads(max_min=25):
    t0 = time.time()
    while time.time() - t0 < max_min * 60:
        txt = bootlog()
        if "ALL DOWNLOADS DONE" in txt:
            for ln in txt.splitlines():
                if "DOWNLOADED" in ln or "ALL DOWNLOADS" in ln or "FAILED" in ln:
                    print("  ", ln[:150], flush=True)
            return
        time.sleep(15)
    raise SystemExit("downloads not finished in %d min" % max_min)


def submit(graph):
    r = post("/prompt", {"prompt": graph, "client_id": "bake"})
    if "prompt_id" not in r:
        return None, r
    return r["prompt_id"], None


def run_graph(graph, timeout_min=20):
    pid, err = submit(graph)
    if err:
        return None, err
    t0 = time.time()
    while time.time() - t0 < timeout_min * 60:
        time.sleep(3)
        try:
            h = get("/history/" + pid)
        except Exception:
            continue
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                return None, {"execution_error": (msgs[0][1] if msgs else st)}
            return {"seconds": time.time() - t0, "outputs": h[pid]["outputs"]}, None
    return None, {"timeout": True}


def vram_used_gb():
    s = get("/system_stats")["devices"][0]
    return round((s["vram_total"] - s["vram_free"]) / 2**30, 1)


def pull_output(res, local):
    for node, o in res["outputs"].items():
        for key in ("images", "gifs", "videos"):
            for f in o.get(key, []):
                if str(f["filename"]).lower().endswith((".mp4", ".webm", ".mov")):
                    q = f"?filename={f['filename']}&subfolder={f.get('subfolder','')}&type={f.get('type','output')}"
                    b = get("/view" + q, raw=True, timeout=300)
                    open(local, "wb").write(b)
                    return len(b)
    return 0


def save_video_nodes(src_images, fps, prefix, variant, audio=None):
    cv = {"images": src_images, "fps": float(fps)}
    if audio:
        cv["audio"] = audio
    nodes = {"cv": {"class_type": "CreateVideo", "inputs": cv}}
    sv = {"video": ["cv", 0], "filename_prefix": prefix}
    sv.update(variant)
    nodes["sv"] = {"class_type": "SaveVideo", "inputs": sv}
    return nodes


SAVE_VARIANTS = [{"format": "auto", "format.codec": "auto"}, {"format": "auto"}, {"format": "mp4", "codec": "h264"}]


def wan_graph(shot, image_name, variant):
    c = {"prompt": f"{shot['description']} {shot['prompt']}", "negative": "", "seed": 42 + shot["ord"], "frames": shot.get("wan_frames", 81), "fps": 16, "width": shot.get("w", 1280), "height": shot.get("h", 720), "image": "x"}
    assert not wan.validate_clip(c), wan.validate_clip(c)
    g = wan.clip_graph(c, image_name, "bake/wan")
    g.pop("16")
    g.update(save_video_nodes(["15", 0], 16, f"bake/wan_shot{shot['ord']:02d}", variant))
    return g


LTX_UNET = "ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"
LTX_TEXT = "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
LTX_CKPT = LTX_UNET
LTX_VAE = "ltx-2.5-video-vae-bf16.safetensors"
LTX_AVAE = "ltx-2.5-audio-vae-bf16.safetensors"
LTX_SIGMAS = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"


def ltx_graph(shot, image_name, variant, w=1280, h=704, frames=121, fps=24):
    w = shot.get("lw", w); h = shot.get("lh", h); frames = shot.get("ltx_frames", frames)
    prompt = f"{shot['description']} {shot['prompt']}"
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": LTX_UNET, "weight_dtype": "default"}},
        "2": {"class_type": "LTXAVTextEncoderLoader", "inputs": {"text_encoder": LTX_TEXT, "ckpt_name": LTX_CKPT, "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": LTX_VAE}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": LTX_AVAE}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["2", 0]}},
        "7": {"class_type": "LTXVConditioning", "inputs": {"positive": ["5", 0], "negative": ["6", 0], "frame_rate": float(fps)}},
        "8": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "9": {"class_type": "LTXVPreprocess", "inputs": {"image": ["8", 0], "img_compression": 35}},
        "10": {"class_type": "LTXVImgToVideo", "inputs": {"positive": ["7", 0], "negative": ["7", 1], "vae": ["3", 0], "image": ["9", 0],
                                                          "width": w, "height": h, "length": frames, "batch_size": 1, "strength": 1.0}},
        "11": {"class_type": "LTXVEmptyLatentAudio", "inputs": {"frames_number": frames, "frame_rate": fps, "batch_size": 1, "audio_vae": ["4", 0]}},
        "12": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["10", 2], "audio_latent": ["11", 0]}},
        "13": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "14": {"class_type": "ManualSigmas", "inputs": {"sigmas": LTX_SIGMAS}},
        "15": {"class_type": "RandomNoise", "inputs": {"noise_seed": 42 + shot["ord"]}},
        "16": {"class_type": "CFGGuider", "inputs": {"model": ["1", 0], "positive": ["10", 0], "negative": ["10", 1], "cfg": 1.0}},
        "17": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["15", 0], "guider": ["16", 0], "sampler": ["13", 0], "sigmas": ["14", 0], "latent_image": ["12", 0]}},
        "18": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["17", 0]}},
        "19": {"class_type": "VAEDecode", "inputs": {"samples": ["18", 0], "vae": ["3", 0]}},
        "20": {"class_type": "LTXVAudioVAEDecode", "inputs": {"samples": ["18", 1], "audio_vae": ["4", 0]}},
    }
    g.update(save_video_nodes(["19", 0], fps, f"bake/ltx_shot{shot['ord']:02d}", variant, audio=["20", 0]))
    return g


def pick_variant(build, shot, image_name):
    last = None
    for v in SAVE_VARIANTS:
        g = build(shot, image_name, v)
        r = post("/prompt", {"prompt": g, "client_id": "bake", "partial_execution_targets": []}, timeout=60) if False else None
        # validate by a dry submission: ComfyUI validates on submit; a valid graph starts running, so we simply run it
        pid, err = submit(g)
        if err is None:
            return v, pid
        last = err
        if "SaveVideo" not in json.dumps(err) and "sv" not in json.dumps(err):
            return None, err
    return None, last


def main():
    model = sys.argv[1]
    keep = "--keep" in sys.argv
    sel = None
    if "--shots" in sys.argv:
        sel = [int(x) for x in sys.argv[sys.argv.index("--shots") + 1].split(",")]
    shots = [s for s in SHOTS if sel is None or s["ord"] in sel]
    print("waiting for downloads...", flush=True)
    wait_downloads()
    results = []
    resf = os.path.join(OUT, f"{TAG}results_{model}.json")
    for i, shot in enumerate(shots):
        img = upload(shot["image"])
        build = wan_graph if model == "wan" else ltx_graph
        variant, pid = pick_variant(build, shot, img)
        if variant is None:
            print("GRAPH REJECTED:", json.dumps(pid)[:2500], flush=True)
            break
        t0 = time.time()
        res = None
        while time.time() - t0 < 25 * 60:
            time.sleep(3)
            h = get("/history/" + pid)
            if pid in h:
                st = h[pid].get("status", {})
                if st.get("status_str") == "error":
                    msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                    print("EXECUTION ERROR:", json.dumps(msgs[0][1] if msgs else st)[:2500], flush=True)
                    break
                res = {"seconds": time.time() - t0, "outputs": h[pid]["outputs"]}
                break
        if not res:
            break
        local = os.path.join(OUT, f"{TAG}{model}_shot{shot['ord']:02d}.mp4")
        size = pull_output(res, local)
        row = {"model": model, "shot": shot["ord"], "seconds": round(res["seconds"], 1), "vram_gb_after": vram_used_gb(), "bytes": size, "first_run": i == 0}
        results.append(row)
        json.dump(results, open(resf, "w"), indent=1)
        print("DONE", row, flush=True)
    print("RESULTS", json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
