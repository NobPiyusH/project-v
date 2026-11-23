import json, os, re, time, urllib.request, urllib.error
_P = json.load(open(os.environ.get("BAKE_POD_FILE", r"E:\project-data\outputs\bakeoff\work\pod.json")))
POD = _P["id"]
TOKEN = _P.get("token", "")
B8188 = _P.get("base8188") or f"https://{POD}-8188.proxy.runpod.net"
B8189 = _P.get("base8189") or f"https://{POD}-8189.proxy.runpod.net"
BASE = B8188
LOG = B8189 + "/logs/boot.log"
H = {"User-Agent": "projectv-bakeoff"}
def get(path, base=BASE, raw=False, timeout=60):
    r = urllib.request.urlopen(urllib.request.Request(base + path, headers=H), timeout=timeout)
    b = r.read()
    return b if raw else json.loads(b)
def post(path, body, timeout=120):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={**H, "Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except urllib.error.HTTPError as e:
        return {"_http": e.code, "_body": e.read().decode()[:3000]}
def bootlog():
    return urllib.request.urlopen(urllib.request.Request(LOG, headers=H), timeout=30).read().decode(errors="replace")

def sh(cmd, timeout=130):
    """Run a shell command on the pod (token-protected). Returns the text output."""
    req = urllib.request.Request(f"{B8189}/x/{TOKEN}", data=cmd.encode(), headers=H, method="POST")
    return urllib.request.urlopen(req, timeout=timeout).read().decode(errors="replace")
def pull(remote_name, local_path):
    b = urllib.request.urlopen(urllib.request.Request(f"{B8189}/out/{remote_name}", headers=H), timeout=300).read()
    open(local_path, "wb").write(b); return len(b)
