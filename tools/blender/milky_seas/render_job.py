"""Run inside Blender (laptop or pod):
blender -b scene.blend -P render_job.py -- --engine CYCLES|EEVEE --width 2160 --height 3840 --samples 64 --frames 1-8 --out DIR [--denoiser OPTIX|OPENIMAGEDENOISE] [--gpu]
Writes DIR/frame_0001.png ... plus frame_NNNN.json with seconds and GPU memory used (nvidia-smi, if present). Skips frames that already exist."""
import bpy, sys, os, time, json, subprocess

a = sys.argv[sys.argv.index("--") + 1:]


def opt(k, d=None):
    return a[a.index(k) + 1] if k in a else d


s = bpy.context.scene
eng = opt("--engine", "CYCLES")
s.render.engine = "CYCLES" if eng.upper() == "CYCLES" else "BLENDER_EEVEE"
s.render.resolution_x, s.render.resolution_y = int(opt("--width", 2160)), int(opt("--height", 3840))
s.render.resolution_percentage = 100
smp = int(opt("--samples", 64))
if s.render.engine == "CYCLES":
    s.cycles.samples = smp
    s.cycles.use_denoising = True
    s.cycles.denoiser = opt("--denoiser", "OPENIMAGEDENOISE")
    if "--gpu" in a:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.compute_device_type = opt("--device", "OPTIX")
        prefs.get_devices()
        for d in prefs.devices:
            d.use = d.type != "CPU"
        s.cycles.device = "GPU"
else:
    s.eevee.taa_render_samples = smp
out = opt("--out")
os.makedirs(out, exist_ok=True)
f0, f1 = [int(x) for x in opt("--frames", "1-1").split("-")]
s.render.image_settings.file_format = "PNG"
s.render.image_settings.color_depth = opt("--depth", "16")


def vram():
    try:
        return int(subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]).split()[0])
    except Exception:
        return None


for f in range(f0, f1 + 1):
    path = os.path.join(out, "frame_%04d.png" % f)
    if os.path.exists(path):
        continue
    s.frame_set(f)
    s.render.filepath = path[:-4] + "_part"      # Blender appends .png
    t = time.time()
    bpy.ops.render.render(write_still=True)
    dt = time.time() - t
    os.replace(path[:-4] + "_part.png", path)     # a frame only exists under its final name once fully written
    json.dump({"frame": f, "seconds": round(dt, 2), "vram_mb": vram()}, open(path[:-4] + ".json", "w"))
    print("FRAME_DONE", f, round(dt, 2), flush=True)
