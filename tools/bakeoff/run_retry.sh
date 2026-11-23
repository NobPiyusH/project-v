#!/bin/bash
# One RunPod pod, monitored; shots 5,12,22 on Wan then LTX per shot; ALWAYS terminates the pod at the end.
B=/e/project-data/outputs/bakeoff
cd /c/dev/project-v
finish() {
  uv run python - <<'PY'
import json, re, urllib.request
e = {}
for l in open(r"C:\dev\project-v\.env", encoding="utf-8"):
    m = re.match(r"^([A-Z0-9_]+)=(.*)$", l.strip())
    if m: e[m.group(1)] = m.group(2).strip().strip('"')
p = r"E:\project-data\outputs\bakeoff\work\pod.json"
d = json.load(open(p))
body = {"query": "mutation($input: PodTerminateInput!){ podTerminate(input:$input) }", "variables": {"input": {"podId": d["id"]}}}
req = urllib.request.Request("https://api.runpod.io/graphql?api_key=" + e["RUNPOD_API_KEY"], data=json.dumps(body).encode(), method="POST", headers={"Content-Type": "application/json", "User-Agent": "pv"})
try: print("terminate:", urllib.request.urlopen(req, timeout=30).read().decode()[:80])
except Exception as ex: print("terminate error", type(ex).__name__)
d["released"] = True; json.dump(d, open(p, "w"))
PY
  [ -n "$MON" ] && kill $MON 2>/dev/null
}
trap finish EXIT
unset BAKE_POD_FILE BAKE_TAG
export BAKE_SHOTS=shots_vast.json
uv run python $B/work/pod_up.py > $B/work/pod_up.log 2>&1
grep -E "RENTED|COMFYUI|could not" $B/work/pod_up.log
grep -q "COMFYUI UP" $B/work/pod_up.log || exit 1
uv run python $B/work/monitor.py > /dev/null 2>&1 &
MON=$!
for s in 5 12 22; do
  for m in wan ltx; do
    [ -f $B/${m}_shot$(printf %02d $s).mp4 ] && continue
    uv run python $B/work/run_test.py $m --shots $s 2>&1 | tee -a $B/work/run_retry.log | grep -E "DONE|ERROR|REJECT|Error" | cut -c1-300
    [ -f $B/${m}_shot$(printf %02d $s).mp4 ] || { echo "STOP: $m shot $s produced no file (pod probably died)"; exit 2; }
  done
done
echo ALL DONE
