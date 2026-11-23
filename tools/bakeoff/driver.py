"""Run shots 17,24,31 on wan and ltx; if the pod dies, rent a new one (max 3 pods). Always terminates the pod at the end."""
import json, os, re, subprocess, sys, time, urllib.request

B = r"E:\project-data\outputs\bakeoff"
W = os.path.join(B, "work")
LOG = os.path.join(W, "driver.log")
KEY = [re.match(r"^RUNPOD_API_KEY=(.*)$", l.strip()).group(1).strip().strip('"') for l in open(r"C:\dev\project-v\.env", encoding="utf-8") if l.startswith("RUNPOD_API_KEY=")][0]
SHOTS = [17, 24, 31]
MAX_PODS = 3


def say(m):
    open(LOG, "a").write(time.strftime("%H:%M:%S ") + m + "\n")
    print(m, flush=True)


def terminate():
    try:
        pid = json.load(open(os.path.join(W, "pod.json")))["id"]
        body = {"query": "mutation($input: PodTerminateInput!){ podTerminate(input:$input) }", "variables": {"input": {"podId": pid}}}
        req = urllib.request.Request("https://api.runpod.io/graphql?api_key=" + KEY, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "projectv-bakeoff"})
        say("terminate " + pid + " -> " + urllib.request.urlopen(req, timeout=30).read().decode()[:100])
    except Exception as e:  # noqa: BLE001
        say("terminate error " + type(e).__name__)


def release():
    p = os.path.join(W, "pod.json")
    d = json.load(open(p)); d["released"] = True; json.dump(d, open(p, "w"))


def done(model, shot):
    return os.path.exists(os.path.join(B, f"{model}_shot{shot:02d}.mp4"))


todo = [(m, s) for s in SHOTS for m in ("wan", "ltx") if not done(m, s)]
pods = 0
try:
    while todo and pods < MAX_PODS:
        pods += 1
        say(f"renting pod #{pods}")
        r = subprocess.run([sys.executable, os.path.join(W, "pod_up.py")], capture_output=True, text=True, creationflags=0x08000000)
        say("pod_up: " + " | ".join(l for l in r.stdout.splitlines() if "RENTED" in l or "COMFYUI" in l or "could not" in l))
        if "COMFYUI UP" not in r.stdout:
            terminate(); release(); continue
        for model, shot in list(todo):
            r = subprocess.run([sys.executable, os.path.join(W, "run_test.py"), model, "--shots", str(shot)], capture_output=True, text=True, creationflags=0x08000000)
            row = [l for l in r.stdout.splitlines() if l.startswith("DONE")]
            say(f"{model} shot {shot}: " + (row[0] if row else "FAILED " + (r.stdout + r.stderr)[-200:].replace("\n", " ")))
            if done(model, shot):
                todo.remove((model, shot))
            else:
                break  # pod probably died: get a new one
        terminate(); release()
finally:
    terminate(); release()
    say("left over: " + str(todo))
