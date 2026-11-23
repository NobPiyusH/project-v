"""Vast run with full GPU logging: rent -> monitor (GPU/RAM/disk/ComfyUI every 15 s -> vast_gpu.log) -> shots 5,12,22 on wan+ltx -> save Vast's own logs -> destroy."""
import json, os, re, subprocess, sys, threading, time, urllib.request, urllib.error

W = r"E:\project-data\outputs\bakeoff\work"
B = r"E:\project-data\outputs\bakeoff"
STATE = os.path.join(W, "pod_vast.json")
GPULOG = os.path.join(W, "vast_gpu.log")
APILOG = os.path.join(W, "vast_instance_logs.txt")
RUNLOG = os.path.join(W, "vast_run.log")
env = {**os.environ, "BAKE_POD_FILE": STATE, "BAKE_TAG": "vast_", "BAKE_SHOTS": "shots_vast.json"}
sys.path.insert(0, W)
stop = threading.Event()


def say(m):
    open(RUNLOG, "a").write(time.strftime("%H:%M:%S ") + m + "\n"); print(m, flush=True)


def vcall(method, path, body=None, ver="v0"):
    d = json.load(open(STATE))
    req = urllib.request.Request(f"https://console.vast.ai/api/{ver}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + d["session_key"], "Content-Type": "application/json", "User-Agent": "pv"})
    try:
        return urllib.request.urlopen(req, timeout=40).read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return "HTTP %s %s" % (e.code, e.read().decode(errors="replace")[:200])


def save_api_logs(tag):
    try:
        d = json.load(open(STATE))
        r = json.loads(vcall("PUT", "/instances/request_logs/%s/" % d["id"], {"tail": "4000"}))
        url = r.get("result_url")
        if url:
            time.sleep(6)
            txt = urllib.request.urlopen(url, timeout=40).read().decode(errors="replace")
            open(APILOG, "a").write(f"\n===== {tag} {time.strftime('%H:%M:%S')} =====\n" + txt)
            say(f"saved Vast instance logs ({tag}, {len(txt)} chars)")
    except Exception as e:  # noqa: BLE001
        say(f"vast api logs failed ({tag}): {type(e).__name__}")


CMD = ("echo $(date +%T) GPU: $(nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,clocks.sm,pstate --format=csv,noheader | tr -d '\\n') "
       "RAM: $(free -m | awk 'NR==2{print $3\"/\"$2\"MB_used\"}') DISK: $(df -m /workspace | awk 'NR==2{print $3\"/\"$2\"MB\"}') "
       "LOAD: $(cut -d' ' -f1-3 /proc/loadavg) ; tail -n 1 /workspace/logs/comfy.log | cut -c1-140")


def monitor():
    from cu import sh
    n = 0
    while not stop.is_set():
        try:
            r = sh(CMD, timeout=40)
        except Exception as e:  # noqa: BLE001
            r = "UNREACHABLE " + type(e).__name__
        open(GPULOG, "a").write(time.strftime("%H:%M:%S ") + r.replace("\n", " | ") + "\n")
        n += 1
        if n % 8 == 0:
            save_api_logs("periodic")
        stop.wait(15)


try:
    for f in (STATE,):
        if os.path.exists(f):
            os.remove(f)
    r = subprocess.run([sys.executable, os.path.join(W, "vast_up.py")], capture_output=True, text=True, creationflags=0x08000000)
    say("vast_up: " + " | ".join(l for l in r.stdout.splitlines() if "RENTED" in l or "COMFYUI" in l or "could not" in l or "offer" in l or "not " in l))
    if "COMFYUI UP" not in r.stdout:
        save_api_logs("rent-failed")
        raise SystemExit("vast not up")
    th = threading.Thread(target=monitor, daemon=True); th.start()
    for shot in (5, 12, 22):
        for model in ("wan", "ltx"):
            t = time.time()
            r = subprocess.run([sys.executable, os.path.join(W, "run_test.py"), model, "--shots", str(shot)], env=env, capture_output=True, text=True, creationflags=0x08000000)
            row = [l for l in r.stdout.splitlines() if l.startswith("DONE")]
            say(f"vast {model} shot {shot} in {time.time()-t:.0f}s: " + (row[0] if row else "FAILED " + (r.stdout + r.stderr)[-200:].replace("\n", " ")))
            if not row:
                raise SystemExit("stopping after failure")
finally:
    stop.set()
    if os.path.exists(STATE):
        save_api_logs("final")
        out = vcall("DELETE", "/instances/%s/" % json.load(open(STATE))["id"])
        say("destroy: " + out[:100])
        d = json.load(open(STATE)); d["released"] = True; json.dump(d, open(STATE, "w"))
    say("vast run finished")
