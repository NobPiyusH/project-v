"""Install Blender on a GPU box, render the 4K alien scene with EEVEE (vulkan, then opengl) and Cycles-GPU, log GPU use, pull results. usage: bl_bench.py <label> <pod.json>"""
import base64, json, os, sys, time, urllib.request

label, statefile = sys.argv[1], sys.argv[2]
os.environ["BAKE_POD_FILE"] = statefile
sys.path.insert(0, r"E:\project-data\outputs\bakeoff\work")
from cu import sh, B8189, H
OUT = r"E:\project-data\outputs\blender-test\gpu"
os.makedirs(OUT, exist_ok=True)
scene = open(r"E:\project-data\outputs\blender-test\alien_k2_4k.py", "rb").read()

SETUP = r'''#!/bin/bash
export DEBIAN_FRONTEND=noninteractive
mkdir -p /workspace/logs /workspace/out; cd /workspace
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq libegl1 libgl1 libxkbcommon0 libxi6 libxrender1 libsm6 libxxf86vm1 libxfixes3 libvulkan1 libx11-6 xz-utils bc >/dev/null 2>&1
T=$(date +%s)
curl -sL https://download.blender.org/release/Blender5.3/blender-5.3.0-linux-x64.tar.xz -o b.tar.xz && tar xf b.tar.xz && mv blender-5.3.0-linux-x64 blender && rm b.tar.xz
echo "BLENDER READY after $(( $(date +%s)-T ))s download+unpack" > /workspace/logs/bl_setup.txt
'''
RUN = r'''#!/bin/bash
cd /workspace
nvidia-smi --query-gpu=timestamp,name,utilization.gpu,memory.used,temperature.gpu,power.draw,clocks.sm --format=csv -l 3 > /workspace/logs/gpu_bl.csv 2>&1 &
SM=$!
: > /workspace/logs/bench.txt
for MODE in "EEVEE_vulkan:BLENDER_EEVEE:vulkan" "EEVEE_opengl:BLENDER_EEVEE:opengl" "CYCLES_gpu:CYCLES:vulkan"; do
  NAME=${MODE%%:*}; REST=${MODE#*:}; ENG=${REST%%:*}; BK=${REST#*:}
  S=$(date +%s.%N)
  BL_ENGINE=$ENG ./blender/blender --gpu-backend $BK -b -P /workspace/scene.py -- 10 /workspace/out/${NAME}.png 190 > /workspace/logs/${NAME}.log 2>&1
  RC=$?
  echo "$NAME rc=$RC secs=$(echo "$(date +%s.%N)-$S" | bc) png=$( [ -f /workspace/out/${NAME}.png ] && stat -c%s /workspace/out/${NAME}.png || echo none)" >> /workspace/logs/bench.txt
done
kill $SM
echo FINISHED >> /workspace/logs/bench.txt
'''


def put(path, data):
    return sh("echo %s | base64 -d > %s" % (base64.b64encode(data).decode(), path), timeout=100)


def get(url, timeout=120):
    return urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=timeout).read()


def log(m):
    open(os.path.join(OUT, f"{label}_bench_progress.txt"), "a").write(time.strftime("%H:%M:%S ") + m + "\n"); print(label, m, flush=True)


put("/workspace/scene.py", scene); put("/workspace/setup.sh", SETUP.encode()); put("/workspace/run.sh", RUN.encode())
log("files sent; gpu: " + sh("nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader").strip()[-120:])
sh("cd /workspace && nohup bash setup.sh > /workspace/logs/setup.out 2>&1 &", timeout=30)
t0 = time.time()
while time.time() - t0 < 20 * 60:
    time.sleep(15)
    try:
        if "READY" in get(B8189 + "/logs/bl_setup.txt").decode():
            break
    except Exception:
        pass
else:
    log("blender setup timed out"); sys.exit(1)
log("blender installed " + get(B8189 + "/logs/bl_setup.txt").decode().strip())
sh("cd /workspace && nohup bash run.sh > /workspace/logs/run.out 2>&1 &", timeout=30)
t0 = time.time()
while time.time() - t0 < 60 * 60:
    time.sleep(20)
    try:
        b = get(B8189 + "/logs/bench.txt").decode()
        if "FINISHED" in b:
            break
    except Exception:
        pass
b = get(B8189 + "/logs/bench.txt").decode()
open(os.path.join(OUT, f"{label}_bench.txt"), "w").write(b); log("bench:\n" + b)
for n in ("EEVEE_vulkan", "EEVEE_opengl", "CYCLES_gpu"):
    try:
        open(os.path.join(OUT, f"{label}_{n}.png"), "wb").write(get(B8189 + f"/out/{n}.png", timeout=300))
    except Exception as e:  # noqa: BLE001
        log(f"no png for {n} ({type(e).__name__})")
    try:
        open(os.path.join(OUT, f"{label}_{n}.log"), "wb").write(get(B8189 + f"/logs/{n}.log")[-6000:])
    except Exception:
        pass
try:
    open(os.path.join(OUT, f"{label}_gpu_usage.csv"), "wb").write(get(B8189 + "/logs/gpu_bl.csv"))
except Exception:
    pass
log("done; pod kept running as requested")
