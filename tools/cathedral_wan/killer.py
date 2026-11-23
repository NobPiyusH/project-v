"""Independent safety net: deletes the pod at the deadline (or earlier if pod.json says released). Never prints keys."""
import json, os, re, sys, time, urllib.request

pod_id, seconds = sys.argv[1], int(sys.argv[2])
state = sys.argv[3] if len(sys.argv) > 3 else "E:/project-data/outputs/cathedral-wan/work/pod.json"
log = r"E:\project-data\outputs\cathedral-wan\work\killer.log"
key = [re.match(r"^RUNPOD_API_KEY=(.*)$", l.strip()).group(1).strip().strip('"') for l in open(r"C:\dev\project-v\.env", encoding="utf-8") if l.startswith("RUNPOD_API_KEY=")][0]


def say(m):
    open(log, "a").write(time.strftime("%H:%M:%S ") + m + "\n")


say(f"watching pod {pod_id}, deadline in {seconds}s")
end = time.time() + seconds
while time.time() < end:
    time.sleep(30)
    try:
        if json.load(open(state)).get("released"):
            say("released by the orchestrator, exiting")
            sys.exit(0)
    except Exception:
        pass
for attempt in range(5):
    try:
        body = {"query": "mutation($input: PodTerminateInput!){ podTerminate(input:$input) }", "variables": {"input": {"podId": pod_id}}}
        req = urllib.request.Request("https://api.runpod.io/graphql?api_key=" + key, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "projectv-bakeoff"})
        out = urllib.request.urlopen(req, timeout=30).read().decode()
        say("DEADLINE: terminate sent -> " + out[:120])
        break
    except Exception as e:  # noqa: BLE001
        say(f"terminate attempt {attempt+1} failed: {type(e).__name__} {str(e)[:100]}")
        time.sleep(10)
