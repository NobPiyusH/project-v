import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from podlib import *
from workers.pod import server as ps

oi = get("/object_info")
for n in ("CreateVideo", "SaveVideo", "LoadVideo", "GetVideoComponents", "ImageUpscaleWithModel", "UpscaleModelLoader", "ImageScale", "ImageFromBatch", "SaveImage"):
    print(n, n in oi)

STYLE = ("Unreal Engine 5 game cinematic trailer still, Lumen global illumination, ray-traced reflections, volumetric light, "
         "deep contrast, razor sharp detail, subtle film grain, anamorphic lens, palette of burnt orange dusk and teal storm clouds, "
         "no people, no faces, no text")
P = ("the silhouette of a colossal dragon-like leviathan with huge leathery bat membrane wings spread wide and a glowing orange underbelly, "
     "rising behind ruined towers, enormous scale, backlit by a burning orange sun, crepuscular god-rays through storm clouds, "
     "dark ruined skyline in the foreground, a ruined sci-fi city at dusk, ")
for seed in (301, 302, 303):
    name = f"s3_rise_dragon_{seed}"
    outs, dt = run(ps.flux_graph({"prompt": P + STYLE, "width": 864, "height": 1536, "seed": seed}, name), wait_s=300, poll=3)
    n = fetch(outs["13"]["images"][0], OUT + r"\keyframes\raw" + "\\" + name + ".png")
    print("saved", name, n // 1024, "KB", round(dt), "s")
