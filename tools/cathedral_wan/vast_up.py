"""Rent ONE Vast.ai instance for the bake-off (same boot as RunPod), arm a destroy-timer, write pod_vast.json."""
import base64, json, os, re, secrets, subprocess, sys, time, urllib.request, urllib.error

sys.path.insert(0, r"C:\dev\project-v")
WORK = r"E:\project-data\outputs\cathedral-wan\work"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(WORK, "pod.json")
src = open(os.path.join(HERE, "pod_up.py"), encoding="utf-8").read()
ns = {}
# reuse BOOT / SRV / DL / files definitions from pod_up.py without running its rental code
for name in ("SRV", "DL", "BOOT"):
    m = re.search(r"^%s = r'''(.*?)'''" % name, src, re.S | re.M)
    ns[name] = m.group(1)
env = {}
for line in open(r"C:\dev\project-v\.env", encoding="utf-8"):
    m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"')
sys.path.insert(0, HERE)
import vastauth
KEY, HF = vastauth.login(), env["HF_TOKEN"]
from workers.pod import server as wan
files = {n: list(v) for n, v in wan.FILES.items()}
TOKEN = secrets.token_hex(16)
MAX_PRICE, MAX_HOURS = 1.00, 0.75
H = {"Authorization": "Bearer " + KEY, "Content-Type": "application/json", "User-Agent": "projectv-bakeoff"}


def call(method, path, body=None):
    req = urllib.request.Request("https://console.vast.ai/api/v0" + path, data=json.dumps(body).encode() if body is not None else None, headers=H, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        return {"_http": e.code, "_b": e.read().decode()[:400]}


q = {"limit": 12, "type": "on-demand", "verified": {"eq": True}, "rentable": {"eq": True}, "gpu_ram": {"gte": 23000}, "disk_space": {"gte": 100},
     "direct_port_count": {"gte": 4}, "inet_down": {"gte": 300}, "reliability2": {"gte": 0.97}, "dph_total": {"lte": MAX_PRICE},
     "cpu_ram": {"gte": 48000}, "num_gpus": {"eq": 1}, "order": [["dph_total", "asc"]]}
offers = [o for o in call("POST", "/bundles/", q).get("offers", []) if "GB10" not in o["gpu_name"] and "CMP" not in o["gpu_name"] and "RTX 8000" not in o["gpu_name"] and not any(b in o["gpu_name"] for b in ("3090", "3080", "A5000", "Titan", "V100", "P40", "2080", "A4000", "A2000", "Q RTX", "Quadro", "A10"))]
for o in offers[:8]:
    print("offer", o["id"], o["gpu_name"], round(o["dph_total"], 3), o.get("geolocation"), flush=True)
env_vars = {"-p 8188:8188": "1", "-p 8189:8189": "1", "HF_TOKEN": HF, "EXEC_TOKEN": TOKEN,
            "SRV_B64": base64.b64encode(ns["SRV"].encode()).decode(), "FILES_B64": base64.b64encode(json.dumps(files).encode()).decode(),
            "DL_B64": base64.b64encode(ns["DL"].encode()).decode()}
boot = ns["BOOT"].replace("sleep infinity", "sleep infinity")
inst = None
for o in offers[:8]:
    r = call("PUT", "/asks/%s/" % o["id"], {"client_id": "me", "image": "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04", "disk": 100,
             "runtype": "ssh", "env": env_vars, "onstart": boot, "label": "projectv-cathedral-wan"})
    if r.get("success") and r.get("new_contract"):
        inst = {"id": str(r["new_contract"]), "gpu": o["gpu_name"], "costPerHr": o["dph_total"], "provider": "vast"}
        print("RENTED", o["gpu_name"], inst["id"], round(o["dph_total"], 3), flush=True)
        break
    print("  could not rent", o["id"], str(r)[:200], flush=True)
if not inst:
    sys.exit("could not rent any Vast instance")
inst.update({"created": time.time(), "released": False, "token": TOKEN, "session_key": KEY})
json.dump(inst, open(STATE, "w"))
subprocess.Popen([sys.executable, r"C:\\dev\\project-v\\tools\\gpu_lease\\lease.py", "keep", "vast", inst["id"], "projectv-cathedral-wan", "cathedral wan test",
                  "--state", STATE, "--max-minutes", str(max(20, int(MAX_HOURS * 60) - 10))],
                 creationflags=0x00000008 | 0x08000000, close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.Popen([sys.executable, os.path.join(HERE, "killer_vast.py"), inst["id"], str(int(MAX_HOURS * 3600))],
                 creationflags=0x00000008 | 0x00000200, close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("destroy-timer started (deadline %.1f h)" % MAX_HOURS, flush=True)
t0 = time.time()
while time.time() - t0 < 30 * 60:
    time.sleep(20)
    r = call("GET", "/instances/%s/" % inst["id"])
    i = r.get("instances", r)
    if isinstance(i, list):
        i = i[0] if i else {}
    ports = i.get("ports") or {}
    st = i.get("actual_status")
    print("  status", st, flush=True)
    if st == "running" and ports.get("8188/tcp") and ports.get("8189/tcp"):
        ip = i.get("public_ipaddr")
        inst["base8188"] = "http://%s:%s" % (ip, ports["8188/tcp"][0]["HostPort"])
        inst["base8189"] = "http://%s:%s" % (ip, ports["8189/tcp"][0]["HostPort"])
        json.dump(inst, open(STATE, "w"))
        break
else:
    sys.exit("instance not running after 30 min; destroy-timer still armed")

_tg = time.time(); ok = None
while time.time() - _tg < 10 * 60 and ok is None:
    time.sleep(10)
    try:
        out = urllib.request.urlopen(urllib.request.Request(inst["base8189"] + "/x/" + TOKEN, data=b"python3 -c 'import torch;print(\"CUDAOK\" if torch.cuda.is_available() else \"CUDABAD\")' 2>/dev/null", headers={"User-Agent": "x"}, method="POST"), timeout=100).read().decode()
        ok = "CUDAOK" in out if ("CUDAOK" in out or "CUDABAD" in out) else None
    except Exception:
        pass
print("CUDA gate:", ok, flush=True)
if ok is not True:
    call("DELETE", "/instances/%s/" % inst["id"]); inst["released"] = True; json.dump(inst, open(STATE, "w"))
    sys.exit("CUDA gate failed; instance destroyed")
for _ in range(120):
    try:
        urllib.request.urlopen(urllib.request.Request(inst["base8188"] + "/system_stats", headers={"User-Agent": "x"}), timeout=15)
        print("COMFYUI UP after %.0fs" % (time.time() - t0), flush=True)
        break
    except Exception:
        time.sleep(20)
else:
    sys.exit("ComfyUI not answering")
