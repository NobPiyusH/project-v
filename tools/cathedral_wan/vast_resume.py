"""Resume after rent: wait for the Vast instance ports, CUDA gate, wait for ComfyUI. State in cathedral-wan/work/pod.json."""
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vastauth
STATE = r"E:\project-data\outputs\cathedral-wan\work\pod.json"
inst = json.load(open(STATE)); KEY = inst["session_key"]; TOKEN = inst["token"]
def call(method, path):
    req = urllib.request.Request("https://console.vast.ai/api/v0" + path, method=method, headers={"Authorization": "Bearer " + KEY, "User-Agent": "x"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())
t0 = time.time()
while time.time() - t0 < 20 * 60:
    time.sleep(15)
    try:
        r = call("GET", "/instances/%s/" % inst["id"]); i = r.get("instances", r)
        if isinstance(i, list): i = i[0] if i else {}
        i = i or {}
    except Exception as e:
        print("poll err", type(e).__name__, flush=True); continue
    ports = i.get("ports") or {}
    print("status", i.get("actual_status"), flush=True)
    if i.get("actual_status") == "running" and ports.get("8188/tcp") and ports.get("8189/tcp"):
        ip = i["public_ipaddr"]
        inst["base8188"] = "http://%s:%s" % (ip, ports["8188/tcp"][0]["HostPort"]); inst["base8189"] = "http://%s:%s" % (ip, ports["8189/tcp"][0]["HostPort"])
        json.dump(inst, open(STATE, "w")); break
else:
    sys.exit("not running in 20 min")
ok = None; tg = time.time()
while time.time() - tg < 10 * 60 and ok is None:
    time.sleep(10)
    try:
        out = urllib.request.urlopen(urllib.request.Request(inst["base8189"] + "/x/" + TOKEN, data=b"python3 -c 'import torch;print(\"CUDAOK\" if torch.cuda.is_available() else \"CUDABAD\")' 2>/dev/null", headers={"User-Agent": "x"}, method="POST"), timeout=100).read().decode()
        ok = ("CUDAOK" in out) if ("CUDAOK" in out or "CUDABAD" in out) else None
    except Exception:
        pass
print("CUDA gate:", ok, flush=True)
if ok is not True:
    call("DELETE", "/instances/%s/" % inst["id"]); inst["released"] = True; json.dump(inst, open(STATE, "w")); sys.exit("CUDA bad; destroyed")
for _ in range(120):
    try:
        urllib.request.urlopen(urllib.request.Request(inst["base8188"] + "/system_stats", headers={"User-Agent": "x"}), timeout=15)
        print("COMFYUI UP after %.0fs" % (time.time() - t0), flush=True); break
    except Exception:
        time.sleep(20)
