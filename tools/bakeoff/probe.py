import sys, os
sys.path.insert(0, "E:/project-data/outputs/bakeoff/work")
os.environ["BAKE_POD_FILE"] = "E:/project-data/outputs/bakeoff/work/" + sys.argv[1]
from cu import sh
try:
    print(sh(sys.argv[2], timeout=60)[:1500])
except Exception as e:
    print("ERR", type(e).__name__, str(getattr(e, "reason", e))[:80])
