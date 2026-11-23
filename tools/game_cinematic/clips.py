"""Wan 2.2 I2V fp8 clips (4 x 5 s) from the approved keyframes on the already-running pod. Saves an mp4 per clip."""
import os, shutil, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from podlib import *
from workers.pod import server as ps

KF = OUT + r"\keyframes"
W, Hh = 640, 1136
shots = [
    ("s1", "s1_wide_101", "slow cinematic aerial push forward over a ruined sci-fi city skyline at dusk, storm clouds churn and drift, "
                          "god-rays shift through the glowing sky, embers float, a vast creature glides far in the clouds, smooth steady camera, game trailer"),
    ("s2", "s2_avenue_101", "smooth steady camera glides forward down a broken avenue, ground mist rolls past, embers drift upward, "
                            "volumetric light shafts flicker, clouds move in the sky, creature silhouette gently flaps far above, game trailer"),
    ("s3", "s3_rise_dragon_301", "colossal dragon creature slowly rises and spreads its leathery wings, glowing underbelly pulses, sun rays flare behind it, "
                                 "clouds swirl, dust and embers drift, slow camera push upward, epic scale, game trailer"),
    ("s4", "s4_overhead_101", "camera slowly tilts up as the colossal winged creature passes overhead, wings beat slowly, glowing underbelly lights the "
                              "clouds, embers drift upward, tower edges slide past, epic scale, game trailer"),
]
os.makedirs(KF + r"\..\clips", exist_ok=True)
for i, (tag, kf, motion) in enumerate(shots):
    out_mp4 = OUT + rf"\clips\wan_{tag}.mp4"
    if os.path.exists(out_mp4):
        continue
    name = f"gc_{tag}.png"
    upload(KF + rf"\raw\{kf}.png", name)
    g = ps.clip_graph({"prompt": motion, "width": W, "height": Hh, "frames": 81, "seed": 1000 + i + 1}, name, f"gc_{tag}")
    g["20"] = {"class_type": "CreateVideo", "inputs": {"images": ["15", 0], "fps": 16.0}}
    g["21"] = {"class_type": "SaveVideo", "inputs": {"video": ["20", 0], "filename_prefix": f"video/gc_{tag}", "format": "mp4", "codec": "h264"}}
    outs, dt = run(g, wait_s=1500, poll=5)
    vid = None
    for nid, o in outs.items():
        for key in ("images", "videos", "gifs"):
            for it in o.get(key, []) or []:
                if it["filename"].endswith(".mp4"):
                    vid = it
    print(tag, "generation", round(dt), "s", flush=True)
    n = fetch(vid, out_mp4)
    print(tag, "pulled", n // 1024, "KB", flush=True)
print("CLIPS DONE", flush=True)
