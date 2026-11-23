"""Independent safety net: terminates a RunPod pod at a deadline (or exits early when state file says released).
usage: killer.py <pod_id> <seconds> <state.json>. Never prints keys."""
import json, os, re, sys, time, urllib.request

pod_id, seconds, state = sys.argv[1], int(sys.argv[2]), sys.argv[3]
ENV = os.environ.get("PROJECTV_ENV", r"C:\dev\project-v\.env")
LOG = os.path.join(os.path.dirname(state), "killer.log")
key = [re.match(r"^RUNPOD_API_KEY=(.*)$", l.strip()).group(1).strip().strip('"') for l in open(ENV, encoding="utf-8") if l.startswith("RUNPOD_API_KEY=")][0]


def say(m):
    open(LOG, "a").write(time.strftime("%H:%M:%S ") + m + "\n")


say(f"watching pod {pod_id}, deadline in {seconds}s")
end = time.time() + seconds
while time.time() < end:
    time.sleep(20)
    try:
        if pod_id in json.load(open(state)).get("released", []):
            say("released by the orchestrator, exiting")
            sys.exit(0)
    except Exception:
        pass
for attempt in range(6):
    try:
        body = {"query": "mutation($input: PodTerminateInput!){ podTerminate(input:$input) }", "variables": {"input": {"podId": pod_id}}}
        req = urllib.request.Request("https://api.runpod.io/graphql?api_key=" + key, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "projectv-render"})
        say("DEADLINE: terminate sent -> " + urllib.request.urlopen(req, timeout=30).read().decode()[:120])
        break
    except Exception as e:  # noqa: BLE001
        say(f"terminate attempt {attempt + 1} failed: {type(e).__name__}")
        time.sleep(10)
