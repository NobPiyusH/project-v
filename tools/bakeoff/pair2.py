"""Project-picture tests (quality 101, motion 102, face 103, long 104). Several single-model pods in two waves; waits for the earlier pair run to finish; always terminates every pod."""
import json, os, re, subprocess, sys, threading, time, urllib.request

W = r"E:\project-data\outputs\bakeoff\work"
KEY = [re.match(r"^RUNPOD_API_KEY=(.*)$", l.strip()).group(1).strip().strip('"') for l in open(r"C:\dev\project-v\.env", encoding="utf-8") if l.startswith("RUNPOD_API_KEY=")][0]
LOG = os.path.join(W, "pair2.log")
lock = threading.Lock()
WAVES = [[("wan", [101, 102]), ("wan", [103]), ("ltx", [101, 102]), ("ltx", [103])],
         [("wan", [104]), ("ltx", [104])]]


def say(m):
    with lock:
        open(LOG, "a").write(time.strftime("%H:%M:%S ") + m + "\n"); print(m, flush=True)


def terminate(state):
    try:
        d = json.load(open(state))
        body = {"query": "mutation($input: PodTerminateInput!){ podTerminate(input:$input) }", "variables": {"input": {"podId": d["id"]}}}
        req = urllib.request.Request("https://api.runpod.io/graphql?api_key=" + KEY, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "pv"})
        say(f"terminate {d['id']}: " + urllib.request.urlopen(req, timeout=30).read().decode()[:60])
        d["released"] = True; json.dump(d, open(state, "w"))
    except Exception as e:  # noqa: BLE001
        say("terminate error " + type(e).__name__)


def lane(idx, model, shots):
    state = os.path.join(W, f"pod2_{idx}.json")
    env = {**os.environ, "POD_STATE": state, "POD_ONLY": model, "BAKE_POD_FILE": state, "BAKE_SHOTS": "shots_proj.json"}
    tag = f"{model}{shots}"
    try:
        if os.path.exists(state):
            os.remove(state)
        r = subprocess.run([sys.executable, os.path.join(W, "pod_up.py")], env=env, capture_output=True, text=True, creationflags=0x08000000)
        say(f"[{tag}] " + " | ".join(l for l in r.stdout.splitlines() if "RENTED" in l or "COMFYUI" in l or "could not" in l))
        if "COMFYUI UP" not in r.stdout:
            return
        for shot in shots:
            t = time.time()
            r = subprocess.run([sys.executable, os.path.join(W, "run_test.py"), model, "--shots", str(shot)], env=env, capture_output=True, text=True, creationflags=0x08000000)
            row = [l for l in r.stdout.splitlines() if l.startswith("DONE")]
            say(f"[{tag}] shot {shot} in {time.time()-t:.0f}s: " + (row[0] if row else "FAILED " + (r.stdout + r.stderr)[-160:].replace("\n", " ")))
            if not row:
                break
    finally:
        if os.path.exists(state):
            terminate(state)


# wait for the earlier two-pod run to finish (so we never hold more pods than needed)
t0 = time.time()
while time.time() - t0 < 40 * 60:
    try:
        if "pair finished" in open(os.path.join(W, "pair.log")).read():
            break
    except Exception:
        pass
    time.sleep(20)
n = 0
for wave in WAVES:
    ths = []
    for model, shots in wave:
        n += 1
        ths.append(threading.Thread(target=lane, args=(n, model, shots)))
    [t.start() for t in ths]
    [t.join() for t in ths]
say("all waves finished")
