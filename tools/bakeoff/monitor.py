"""Poll the pod every 15 s and append memory/GPU/disk/ComfyUI-log lines to monitor.log on this laptop, so a crash leaves evidence."""
import sys, time
sys.path.insert(0, r"E:\project-data\outputs\bakeoff\work")
from cu import sh
out = r"E:\project-data\outputs\bakeoff\work\monitor.log"
CMD = "echo $(date +%T) $(free -m | awk 'NR==2{print \"ram_used_MB=\"$3\" avail=\"$7}') $(nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader | tr -d ' ') $(df -m /workspace | awk 'NR==2{print \"disk_used_MB=\"$3}') ; tail -n 2 /workspace/logs/comfy.log | cut -c1-160; cat /sys/fs/cgroup/memory.events 2>/dev/null | tr '\\n' ' '"
end = time.time() + 3 * 3600
while time.time() < end:
    try:
        r = sh(CMD, timeout=40)
    except Exception as e:  # noqa: BLE001
        r = "UNREACHABLE " + type(e).__name__
    open(out, "a").write(time.strftime("%H:%M:%S ") + r.replace("\n", " | ") + "\n")
    time.sleep(15)
