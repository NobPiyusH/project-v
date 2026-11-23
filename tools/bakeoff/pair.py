"""Two RunPod pods in parallel, one per model, each downloading only its own files. Shots 12 and 22. Always terminates both pods."""
import json, os, re, subprocess, sys, threading, time, urllib.request

W = r"E:\project-data\outputs\bakeoff\work"
B = r"E:\project-data\outputs\bakeoff"
KEY = [re.match(r"^RUNPOD_API_KEY=(.*)$", l.strip()).group(1).strip().strip('"') for l in open(r"C:\dev\project-v\.env", encoding="utf-8") if l.startswith("RUNPOD_API_KEY=")][0]
LOG = os.path.join(W, "pair.log")
lock = threading.Lock()


def say(m):
    with lock:
        open(LOG, "a").write(time.strftime("%H:%M:%S ") + m + "\n"); print(m, flush=True)


def terminate(state):
    try:
        d = json.load(open(state))
        body = {"query": "mutation($input: PodTerminateInput!){ podTerminate(input:$input) }", "variables": {"input": {"podId": d["id"]}}}
        req = urllib.request.Request("https://api.runpod.io/graphql?api_key=" + KEY, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "pv"})
        say(f"terminate {d['id']}: " + urllib.request.urlopen(req, timeout=30).read().decode()[:70])
        d["released"] = True; json.dump(d, open(state, "w"))
    except Exception as e:  # noqa: BLE001
        say("terminate error " + type(e).__name__)


def lane(model):
    state = os.path.join(W, f"pod_{model}.json")
    env = {**os.environ, "POD_STATE": state, "POD_ONLY": model, "BAKE_POD_FILE": state, "BAKE_SHOTS": "shots_vast.json"}
    try:
        if os.path.exists(state):
            os.remove(state)
        r = subprocess.run([sys.executable, os.path.join(W, "pod_up.py")], env=env, capture_output=True, text=True, creationflags=0x08000000)
        say(f"[{model}] " + " | ".join(l for l in r.stdout.splitlines() if "RENTED" in l or "COMFYUI" in l or "could not" in l))
        if "COMFYUI UP" not in r.stdout:
            return
        for shot in (12, 22):
            t = time.time()
            r = subprocess.run([sys.executable, os.path.join(W, "run_test.py"), model, "--shots", str(shot)], env=env, capture_output=True, text=True, creationflags=0x08000000)
            row = [l for l in r.stdout.splitlines() if l.startswith("DONE")]
            say(f"[{model}] shot {shot} after {time.time()-t:.0f}s: " + (row[0] if row else "FAILED " + (r.stdout + r.stderr)[-160:].replace("\n", " ")))
            if not row:
                break
    finally:
        if os.path.exists(state):
            terminate(state)


ths = [threading.Thread(target=lane, args=(m,)) for m in ("wan", "ltx")]
[t.start() for t in ths]
[t.join() for t in ths]
say("pair finished")
