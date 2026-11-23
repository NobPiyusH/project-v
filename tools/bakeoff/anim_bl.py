import sys, os, base64, time, urllib.request
os.environ["BAKE_POD_FILE"] = "E:/project-data/outputs/bakeoff/work/pod_bl.json"
sys.path.insert(0, "E:/project-data/outputs/bakeoff/work")
from cu import sh, B8189, H
b = base64.b64encode(open("E:/project-data/outputs/blender-test/alien_k2_4k.py","rb").read()).decode()
sh("echo %s | base64 -d > /workspace/scene.py" % b, timeout=100)
cmd = "cd /workspace && (S=$(date +%s.%N); BL_RANGE=100,123 ./blender/blender --gpu-backend vulkan -b -P scene.py -- 10 /workspace/out/anim_4k.mp4 > logs/anim.log 2>&1; echo \"anim24 rc=$? secs=$(echo \"$(date +%s.%N)-$S\" | bc)\" > logs/anim.txt) < /dev/null > /dev/null 2>&1 &"
try: sh("setsid nohup bash -c %r & echo ok" % cmd, timeout=15)
except Exception as e: print("launch", type(e).__name__)
for _ in range(60):
    time.sleep(10)
    try:
        t = urllib.request.urlopen(urllib.request.Request(B8189 + "/logs/anim.txt", headers=H), timeout=20).read().decode()
        print(t); break
    except Exception: pass
