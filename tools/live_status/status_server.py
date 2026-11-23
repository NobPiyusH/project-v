"""Live status page for GPU/Blender work: http://127.0.0.1:8099
Shows RunPod pods (state, price, uptime, GPU use), balance and spend since this page started, the newest rendered frames,
and the tail of the newest log. Read-only: it never starts or stops anything. Keys are read from .env and never sent to the browser.
Run: uv run python tools/live_status/status_server.py"""
import hashlib, io, json, os, re, sys, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8099
RENDER_DIRS = [r"E:\project-data\blender\milky-seas", r"E:\project-data\outputs\blender-test\gpu"]
THUMBS = os.path.join(os.environ.get("TEMP", "."), "live_status_thumbs")
os.makedirs(THUMBS, exist_ok=True)


def env_key(name):
    for line in open(r"C:\dev\project-v\.env", encoding="utf-8"):
        m = re.match(r"^%s=(.*)$" % name, line.strip())
        if m:
            return m.group(1).strip().strip('"')
    return ""


RP_KEY = env_key("RUNPOD_API_KEY")
START = {"balance": None, "time": time.time()}


def runpod():
    q = "{ myself { clientBalance currentSpendPerHr pods { id name desiredStatus costPerHr machine { gpuDisplayName } runtime { uptimeInSeconds gpus { gpuUtilPercent memoryUtilPercent } } } } }"
    req = urllib.request.Request("https://api.runpod.io/graphql?api_key=" + RP_KEY, data=json.dumps({"query": q}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "projectv-status"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())["data"]["myself"]
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}
    if START["balance"] is None:
        START["balance"] = d["clientBalance"]
    pods = []
    for p in d["pods"]:
        rt = p.get("runtime") or {}
        gpu = (rt.get("gpus") or [{}])[0]
        pods.append({"id": p["id"], "name": p["name"], "status": p["desiredStatus"], "price": p["costPerHr"],
                     "gpu": (p.get("machine") or {}).get("gpuDisplayName"), "uptime_min": round((rt.get("uptimeInSeconds") or 0) / 60, 1),
                     "gpu_util": gpu.get("gpuUtilPercent"), "vram_util": gpu.get("memoryUtilPercent")})
    return {"balance": round(d["clientBalance"], 3), "spend_per_hr": d["currentSpendPerHr"],
            "spent_since_start": round(START["balance"] - d["clientBalance"], 3), "pods": pods}


def newest(paths, exts, n):
    found = []
    for root in paths:
        for dp, _, fs in os.walk(root):
            for f in fs:
                if f.lower().endswith(exts):
                    p = os.path.join(dp, f)
                    try:
                        found.append((os.path.getmtime(p), p))
                    except OSError:
                        pass
    return sorted(found, reverse=True)[:n]


def status():
    frames = [{"name": os.path.basename(p), "path": p, "age_min": round((time.time() - t) / 60, 1),
               "size_mb": round(os.path.getsize(p) / 1e6, 2)} for t, p in newest(RENDER_DIRS, (".png", ".jpg", ".jpeg"), 8)]
    logs = newest(RENDER_DIRS, (".log", ".txt"), 1)
    tail = ""
    if logs:
        try:
            tail = "".join(open(logs[0][1], encoding="utf-8", errors="replace").readlines()[-18:])
        except OSError:
            pass
    return {"time": time.strftime("%H:%M:%S"), "runpod": runpod(), "frames": frames, "log_file": logs[0][1] if logs else "", "log_tail": tail}


def thumb(path):
    real = os.path.realpath(path)
    if not any(real.lower().startswith(os.path.realpath(r).lower()) for r in RENDER_DIRS) or not os.path.isfile(real):
        return None
    key = os.path.join(THUMBS, hashlib.md5((real + str(os.path.getmtime(real))).encode()).hexdigest() + ".jpg")
    if not os.path.exists(key):
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        im = Image.open(real).convert("RGB"); im.thumbnail((640, 640)); im.save(key, quality=82)
    return open(key, "rb").read()


PAGE = """<!doctype html><meta charset=utf-8><title>Live status</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0b0f17;--card:#141b27;--ink:#e6edf6;--mut:#8a97aa;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--acc:#7dd3fc}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,Segoe UI,sans-serif;padding:16px 16px 40px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mut);font-size:13px;margin-bottom:14px}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:14px}
.card{background:var(--card);border-radius:12px;padding:14px}.k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.v{font-size:26px;font-weight:600}.pod{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-top:1px solid #243044}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px}
.frames{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}.frames img{width:100%;border-radius:8px;display:block}
.frames div{font-size:12px;color:var(--mut)}pre{background:#0a0e15;border-radius:8px;padding:10px;overflow:auto;font-size:12px;max-height:260px;white-space:pre-wrap}
</style>
<h1>Live GPU and render status</h1><div class=sub id=t>loading...</div>
<div class=row id=top></div>
<div class=card><div class=k>Pods on your RunPod account</div><div id=pods></div></div><br>
<div class=card><div class=k>Newest rendered frames</div><div class=frames id=frames></div></div><br>
<div class=card><div class=k>Newest log: <span id=lf></span></div><pre id=log></pre></div>
<script>
async function tick(){try{const d=await (await fetch('/api/status')).json();const r=d.runpod;
document.getElementById('t').textContent='Updated '+d.time+' - refreshes every 5 s - read only';
let top='';
if(r.error){top='<div class=card><div class=k>RunPod</div><div class=v style="color:var(--bad)">unreachable ('+r.error+')</div></div>'}
else{top='<div class=card><div class=k>Balance</div><div class=v>$'+r.balance+'</div></div>'
+'<div class=card><div class=k>Spent since this page opened</div><div class=v>$'+r.spent_since_start+'</div></div>'
+'<div class=card><div class=k>Spending right now</div><div class=v style="color:'+(r.spend_per_hr>0?'var(--warn)':'var(--ok)')+'">$'+r.spend_per_hr+'/h</div></div>'
+'<div class=card><div class=k>Pods running</div><div class=v style="color:'+(r.pods.length?'var(--warn)':'var(--ok)')+'">'+r.pods.length+'</div></div>'}
document.getElementById('top').innerHTML=top;
document.getElementById('pods').innerHTML=r.error?'':(r.pods.length?r.pods.map(p=>'<div class=pod><span><span class=dot style="background:var(--warn)"></span>'+p.name+' <span style="color:var(--mut)">('+p.id+')</span></span><span>'+(p.gpu||'?')+' - $'+p.price+'/h - up '+p.uptime_min+' min - GPU '+(p.gpu_util??'?')+'% - VRAM '+(p.vram_util??'?')+'%</span></div>').join(''):'<div style="color:var(--ok);padding-top:8px">No pods running. Nothing is costing money.</div>');
document.getElementById('frames').innerHTML=d.frames.length?d.frames.map(f=>'<div><img src="/thumb?p='+encodeURIComponent(f.path)+'" loading=lazy>'+f.name+'<br>'+f.age_min+' min ago - '+f.size_mb+' MB</div>').join(''):'<div>No frames yet.</div>';
document.getElementById('lf').textContent=d.log_file;document.getElementById('log').textContent=d.log_tail||'(no log yet)';
}catch(e){document.getElementById('t').textContent='Status server not answering: '+e}}
tick();setInterval(tick,5000);
</script>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            self.send(200, json.dumps(status()).encode(), "application/json")
        elif self.path.startswith("/thumb"):
            from urllib.parse import parse_qs, urlparse
            t = thumb(parse_qs(urlparse(self.path).query).get("p", [""])[0])
            self.send(200, t, "image/jpeg") if t else self.send(404, b"no", "text/plain")
        else:
            self.send(200, PAGE.encode(), "text/html; charset=utf-8")


if __name__ == "__main__":
    print(f"Live status on http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
