"""Small helpers shared by the game-cinematic scripts: talk to the pod's ComfyUI through the RunPod proxy."""
import json, sys, time, urllib.request

sys.path.insert(0, r"C:\dev\project-v\.claude\worktrees\game-cinematic-20s-847cab")
OUT = r"E:\project-data\outputs\game-cinematic"
POD = json.load(open(OUT + r"\work\pod.json"))["id"]
B = f"https://{POD}-8188.proxy.runpod.net"
H = {"User-Agent": "projectv-gamecine"}


def get(path, raw=False, timeout=60):
    b = urllib.request.urlopen(urllib.request.Request(B + path, headers=H), timeout=timeout).read()
    return b if raw else json.loads(b)


def post(path, body, timeout=120):
    req = urllib.request.Request(B + path, data=json.dumps(body).encode(), headers={**H, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def upload(path, name):
    import uuid
    bd = uuid.uuid4().hex
    data = open(path, "rb").read()
    body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n").encode() + data + \
           (f"\r\n--{bd}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{bd}--\r\n").encode()
    req = urllib.request.Request(B + "/upload/image", data=body, headers={**H, "Content-Type": "multipart/form-data; boundary=" + bd})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def run(graph, wait_s=1500, poll=4):
    """Submit, wait for history outputs, return the outputs dict (node id -> output)."""
    pid = post("/prompt", {"prompt": graph})["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(poll)
        h = get("/history/" + pid)
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") == "error":
                raise RuntimeError("comfy error: " + json.dumps(st)[:1500])
            if h[pid].get("outputs") is not None and st.get("completed"):
                return h[pid]["outputs"], time.time() - t0
    raise TimeoutError("job timeout")


def fetch(item, dest):
    q = "filename=%s&subfolder=%s&type=%s" % (item["filename"], item.get("subfolder", ""), item.get("type", "output"))
    data = get("/view?" + q, raw=True, timeout=600)
    open(dest, "wb").write(data)
    return len(data)
