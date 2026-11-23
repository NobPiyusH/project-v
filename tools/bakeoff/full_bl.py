import sys, os, base64, time, urllib.request
os.environ["BAKE_POD_FILE"] = "E:/project-data/outputs/bakeoff/work/pod_bl.json"
sys.path.insert(0, "E:/project-data/outputs/bakeoff/work")
from cu import sh, B8189, H
OUT = "E:/project-data/outputs/blender-test/gpu"
def put(local, remote):
    sh("echo %s | base64 -d > %s" % (base64.b64encode(open(local, "rb").read()).decode(), remote), timeout=100)
put("E:/project-data/outputs/blender-test/alien_k2_4k.py", "/workspace/scene.py")
put("E:/project-data/outputs/blender-test/random3_4k.py", "/workspace/scene3.py")
RUN = r"""#!/bin/bash
cd /workspace; : > logs/full.txt; T0=$(date +%s)
one() { S=$(date +%s.%N); "$@" > logs/last.log 2>&1; echo "$NAME rc=$? secs=$(echo "$(date +%s.%N)-$S" | bc)" >> logs/full.txt; }
NAME=alien_4k_10s one ./blender/blender --gpu-backend vulkan -b -P scene.py -- 10 /workspace/out/alien_4k_10s.mp4
for w in blackhole meteors city; do NAME=${w}_4k_6s one ./blender/blender --gpu-backend vulkan -b -P scene3.py -- $w 6 /workspace/out/${w}_4k_6s.mp4; done
echo "ALL FINISHED total=$(( $(date +%s)-T0 ))s" >> logs/full.txt
"""
put_b = base64.b64encode(RUN.encode()).decode()
sh("echo %s | base64 -d > /workspace/full.sh" % put_b, timeout=60)
try: sh("cd /workspace && setsid nohup bash full.sh > logs/full.out 2>&1 < /dev/null & echo ok", timeout=15)
except Exception as e: print("launch", type(e).__name__)
print("started", time.strftime("%H:%M:%S"), flush=True)
for _ in range(240):
    time.sleep(15)
    try:
        t = urllib.request.urlopen(urllib.request.Request(B8189 + "/logs/full.txt", headers=H), timeout=20).read().decode()
        if "ALL FINISHED" in t: break
    except Exception: pass
print(t); open(OUT + "/runpod_L40S_full_times.txt", "w").write(t)
for n in ("alien_4k_10s", "blackhole_4k_6s", "meteors_4k_6s", "city_4k_6s"):
    try:
        d = urllib.request.urlopen(urllib.request.Request(B8189 + "/out/%s.mp4" % n, headers=H), timeout=600).read()
        open("%s/runpod_%s.mp4" % (OUT, n), "wb").write(d); print(n, len(d) // 1000000, "MB")
    except Exception as e: print("no", n, type(e).__name__)
