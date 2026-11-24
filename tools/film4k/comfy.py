"""ComfyUI helpers for the film pods: an HTTP client (through the RunPod proxy) and the Real-ESRGAN 4K graph builder."""
from __future__ import annotations

import json
import time
import urllib.request
import uuid

UA = {"User-Agent": "projectv-film4k"}  # RunPod's proxy answers 403 to Python's default agent


def esrgan_graph(video_name: str, prefix: str, w: int = 2160, h: int = 3840, slices=((0, 27), (27, 27), (54, 27))) -> dict:
    """Real-ESRGAN x4plus on one 81-frame beat, in slices: 81 frames x 2880x5120 x float32 would need about 14 GB at once,
    so each slice is upscaled and scaled down to w x h before the slices are joined. Measured 3-5.5 min per beat on a 4090."""
    g = {"1": {"class_type": "LoadVideo", "inputs": {"file": video_name}},
         "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
         "3": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "RealESRGAN_x4plus.pth"}}}
    for j, (start, length) in enumerate(slices):
        g[f"a{j}"] = {"class_type": "ImageFromBatch", "inputs": {"image": ["2", 0], "batch_index": start, "length": length}}
        g[f"b{j}"] = {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["3", 0], "image": [f"a{j}", 0]}}
        g[f"c{j}"] = {"class_type": "ImageScale", "inputs": {"image": [f"b{j}", 0], "upscale_method": "lanczos",
                                                             "width": w, "height": h, "crop": "disabled"}}
    last = "c0"
    for j in range(1, len(slices)):
        g[f"d{j}"] = {"class_type": "ImageBatch", "inputs": {"image1": [last, 0], "image2": [f"c{j}", 0]}}
        last = f"d{j}"
    g["6"] = {"class_type": "CreateVideo", "inputs": {"images": [last, 0], "fps": ["2", 2]}}
    g["7"] = {"class_type": "SaveVideo", "inputs": {"video": ["6", 0], "filename_prefix": f"video/{prefix}", "format": "mp4", "codec": "h264"}}
    return g


def beat_video_nodes(graph: dict, beats: int) -> dict:
    """Turn an SVI scene graph (workers.pod.server.scene_graph) into one that saves an mp4 per beat instead of every PNG frame."""
    for k in range(1, beats + 1):
        for n in (f"s{k}", f"l{k}"):
            graph.pop(n, None)
        graph[f"v{k}"] = {"class_type": "CreateVideo", "inputs": {"images": [f"d{k}", 0], "fps": 16.0}}
        graph[f"w{k}"] = {"class_type": "SaveVideo", "inputs": {"video": [f"v{k}", 0], "filename_prefix": f"video/{k}", "format": "mp4", "codec": "h264"}}
    return graph


class Comfy:
    def __init__(self, base: str, sleep=time.sleep, clock=time.time):
        self.base, self.sleep, self.clock = base, sleep, clock

    def get(self, path: str, raw: bool = False, timeout: int = 60):
        b = urllib.request.urlopen(urllib.request.Request(self.base + path, headers=UA), timeout=timeout).read()
        return b if raw else json.loads(b)

    def post(self, path: str, body: dict, timeout: int = 120):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers={**UA, "Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())

    def upload(self, path: str, name: str, ctype: str = "image/png") -> None:
        bd = uuid.uuid4().hex
        data = open(path, "rb").read()
        body = (f'--{bd}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n').encode() \
            + data + (f'\r\n--{bd}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n--{bd}--\r\n').encode()
        urllib.request.urlopen(urllib.request.Request(self.base + "/upload/image", data=body, headers={
            **UA, "Content-Type": "multipart/form-data; boundary=" + bd}), timeout=300).read()

    def run(self, graph: dict, limit_s: int, say=print, tag: str = "job") -> dict:
        pid = self.post("/prompt", {"prompt": graph})["prompt_id"]
        t0, last = self.clock(), 0.0
        while self.clock() - t0 < limit_s:
            self.sleep(8)
            h = self.get("/history/" + pid)
            if pid in h:
                st = h[pid].get("status", {})
                if st.get("status_str") == "error":
                    raise RuntimeError("comfy error: " + json.dumps(st)[:800])
                if h[pid].get("outputs") and st.get("completed"):
                    say(f"{tag}: done in {self.clock() - t0:.0f}s")
                    return h[pid]["outputs"]
            if self.clock() - last > 180:
                last = self.clock()
                say(f"{tag}: running {last - t0:.0f}s")
        raise TimeoutError(tag)

    @staticmethod
    def mp4s(outputs: dict, nodes) -> list[dict]:
        return [it for n in nodes for key in ("images", "videos", "gifs") for it in (outputs.get(n, {}).get(key) or [])
                if it["filename"].endswith(".mp4")]

    def fetch(self, item: dict, dest: str) -> None:
        q = "filename=%s&subfolder=%s&type=%s" % (item["filename"], item.get("subfolder", ""), item.get("type", "output"))
        open(dest, "wb").write(self.get("/view?" + q, raw=True, timeout=900))
