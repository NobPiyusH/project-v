"""Destroys the Vast instance at the deadline, or exits early if pod_vast.json says released. Never prints keys."""
import json, re, sys, time, urllib.request

iid, seconds = sys.argv[1], int(sys.argv[2])
state = r"E:\project-data\outputs\cathedral-wan\work\pod.json"
log = r"E:\project-data\outputs\cathedral-wan\work\killer_vast.log"
key = json.load(open(state)).get("session_key", "")


def say(m):
    open(log, "a").write(time.strftime("%H:%M:%S ") + m + "\n")


say(f"watching instance {iid}, deadline in {seconds}s")
end = time.time() + seconds
while time.time() < end:
    time.sleep(30)
    try:
        if json.load(open(state)).get("released"):
            say("released, exiting"); sys.exit(0)
    except Exception:
        pass
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
for attempt in range(5):
    if attempt == 2:
        import vastauth; key = vastauth.login()
    try:
        req = urllib.request.Request("https://console.vast.ai/api/v0/instances/%s/" % iid, method="DELETE",
                                     headers={"Authorization": "Bearer " + key, "User-Agent": "projectv-bakeoff"})
        say("DEADLINE: destroy sent -> " + urllib.request.urlopen(req, timeout=30).read().decode()[:100]); break
    except Exception as e:  # noqa: BLE001
        say(f"destroy attempt {attempt+1} failed: {type(e).__name__}"); time.sleep(10)
