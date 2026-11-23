import sys, os, time, urllib.request
os.environ["BAKE_POD_FILE"] = "E:/project-data/outputs/bakeoff/work/pod_bl.json"
sys.path.insert(0, "E:/project-data/outputs/bakeoff/work")
from cu import B8189, H
OUT = "E:/project-data/outputs/blender-test/gpu"
os.makedirs(OUT, exist_ok=True)
def get(p, t=300):
    return urllib.request.urlopen(urllib.request.Request(B8189 + p, headers=H), timeout=t).read()
for _ in range(40):
    b = get("/logs/bench.txt").decode()
    if "FINISHED" in b: break
    time.sleep(10)
print(b)
open(OUT + "/runpod_L40S_bench.txt", "w").write(b)
for n in ("EEVEE_vulkan", "EEVEE_opengl", "CYCLES_gpu"):
    try: open(f"{OUT}/runpod_L40S_{n}.png", "wb").write(get(f"/out/{n}.png"))
    except Exception as e: print("no png", n, type(e).__name__)
    try:
        t = get(f"/logs/{n}.log").decode(errors="replace"); open(f"{OUT}/runpod_L40S_{n}.log", "w").write(t[-8000:])
        print(n, [l.strip()[:110] for l in t.splitlines() if "Time:" in l or "rror" in l][-3:])
    except Exception: pass
try: open(OUT + "/runpod_L40S_gpu_usage.csv", "wb").write(get("/logs/gpu_bl.csv"))
except Exception: pass
