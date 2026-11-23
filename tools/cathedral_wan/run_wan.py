"""Run Wan 2.2 I2V fp8 on the cathedral anchors. usage: run_wan.py [WxH]  (pod must be up; state in cathedral-wan/work/pod.json)"""
import json, os, sys, time, uuid, urllib.request
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, r"C:\dev\project-v")
from cu import *
from workers.pod import server as wan
from PIL import Image

OUT = r"E:\project-data\outputs\cathedral-wan"
SRC = r"E:\project-data\blender\library\cathedral\anchors_4k"
W, H_ = (int(x) for x in (sys.argv[1] if len(sys.argv) > 1 else "720x1280").split("x"))
BASE_P = ("dark cinematic gothic cathedral interior, photoreal, moody high-contrast lighting, rose window glowing with deep stained glass colours, "
          "flickering candle flames on the rows of candles, drifting dust motes in volumetric light shafts, real hooded people with natural varied "
          "proportions swaying slightly, volumetric fog, slow smooth camera glide forward, keep the composition and layout of the first frame, "
          "35mm film look, shallow depth, realistic stone texture")
SHOTS = [{"ord": 2, "file": "anchor_t05_00s_4k.png", "extra": "view down the nave toward the rose window between rows of candles"},
         {"ord": 3, "file": "anchor_t10_00s_4k.png", "extra": "view rising up toward the rose window high above the nave"}]
NEG = "cartoon, 3d render, plastic, toy, pawn figures, identical people, flat colours, candy colours, neon, painting, illustration, brick pattern wall, flat fog"

def upload(path):
    name = "cwan_" + os.path.basename(path); bound = uuid.uuid4().hex
    body = (f"--{bound}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode() \
        + open(path, "rb").read() + (f"\r\n--{bound}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{bound}--\r\n").encode()
    req = urllib.request.Request(BASE + "/upload/image", data=body, headers={**H, "Content-Type": "multipart/form-data; boundary=" + bound}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=120).read())["name"]

def wait_downloads(max_min=20):
    t0 = time.time()
    while time.time() - t0 < max_min * 60:
        txt = bootlog()
        if "ALL DOWNLOADS DONE" in txt:
            print([l[:120] for l in txt.splitlines() if "DOWNLOADED" in l or "FAILED" in l], flush=True); return
        time.sleep(15)
    raise SystemExit("downloads not done")

wait_downloads()
os.makedirs(OUT + r"\in", exist_ok=True); os.makedirs(OUT + r"\frames", exist_ok=True)
for s in SHOTS:
    im = Image.open(os.path.join(SRC, s["file"])).convert("RGB").resize((W, H_), Image.LANCZOS)
    local = OUT + rf"\in\anchor{s['ord']}_{W}x{H_}.png"; im.save(local)
    name = upload(local)
    c = {"prompt": f"{s['extra']}. {BASE_P}", "negative": NEG, "seed": 1000 + s["ord"], "frames": 81, "fps": 16, "width": W, "height": H_, "image": "x"}
    assert not wan.validate_clip(c), wan.validate_clip(c)
    g = wan.clip_graph(c, name, f"cwan/a{s['ord']}")
    r = post("/prompt", {"prompt": g, "client_id": "cwan"})
    if "prompt_id" not in r: print("REJECTED", json.dumps(r)[:1500]); sys.exit(2)
    pid = r["prompt_id"]; t0 = time.time(); print("submitted", s["ord"], pid, flush=True)
    while time.time() - t0 < 20 * 60:
        time.sleep(4)
        h = get("/history/" + pid)
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
                print("EXEC ERROR", json.dumps(msgs[0][1] if msgs else st)[:1500]); sys.exit(3)
            files = [f for o in h[pid]["outputs"].values() for f in o.get("images", [])]
            print("done", s["ord"], round(time.time() - t0), "s frames", len(files), flush=True)
            for f in files:
                q = f"?filename={f['filename']}&subfolder={f.get('subfolder','')}&type={f.get('type','output')}"
                open(OUT + rf"\frames\a{s['ord']}_{f['filename']}", "wb").write(get("/view" + q, raw=True, timeout=300))
            break
    else:
        print("TIMEOUT"); sys.exit(4)
print("ALL DONE")
