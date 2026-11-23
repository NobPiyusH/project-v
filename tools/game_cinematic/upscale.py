"""Real-ESRGAN x4plus (ComfyUI core nodes) then Lanczos to 2160x3840, per clip, on the pod. Usage: upscale.py s1 [s2 ...]"""
import os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from podlib import *

for tag in sys.argv[1:]:
    dest = OUT + rf"\clips\up4k_{tag}.mp4"
    if os.path.exists(dest):
        continue
    # LoadVideo reads from ComfyUI's input dir: upload the local 640x1136 clip is small, so push it with the upload endpoint
    import uuid, urllib.request
    bd = uuid.uuid4().hex
    data = open(OUT + rf"\clips\wan_{tag}.mp4", "rb").read()
    body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"gc_{tag}.mp4\"\r\nContent-Type: video/mp4\r\n\r\n").encode() + data + \
           (f"\r\n--{bd}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{bd}--\r\n").encode()
    urllib.request.urlopen(urllib.request.Request(B + "/upload/image", data=body, headers={**H, "Content-Type": "multipart/form-data; boundary=" + bd}), timeout=300).read()
    g = {
        "1": {"class_type": "LoadVideo", "inputs": {"file": f"gc_{tag}.mp4"}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "RealESRGAN_x4plus.pth"}},
        "4": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["3", 0], "image": ["2", 0]}},
        "5": {"class_type": "ImageScale", "inputs": {"image": ["4", 0], "upscale_method": "lanczos", "width": 2160, "height": 3840, "crop": "disabled"}},
        "6": {"class_type": "CreateVideo", "inputs": {"images": ["5", 0], "fps": ["2", 2]}},
        "7": {"class_type": "SaveVideo", "inputs": {"video": ["6", 0], "filename_prefix": f"video/up4k_{tag}", "format": "mp4", "codec": "h264"}},
    }
    outs, dt = run(g, wait_s=1200, poll=5)
    vid = [it for o in outs.values() for k in ("images", "videos", "gifs") for it in (o.get(k) or []) if it["filename"].endswith(".mp4")][-1]
    print(tag, "upscale", round(dt), "s", flush=True)
    print(tag, "pulled", fetch(vid, dest) // 1024, "KB", flush=True)
