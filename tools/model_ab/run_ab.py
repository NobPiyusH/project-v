"""Same-prompt, same-image test across cheap video models (RunPod public endpoints).

Usage:
  uv run python tools/model_ab/run_ab.py --out E:/project-data/outputs/model-ab/<name> \
      --image-prompt "photoreal start frame ..." --motion "what moves ..." [--only pvideo,seedance480]
  (or --image-url <existing url> to skip the start image)

Writes start.png, <model>.mp4, log.jsonl, a blind side-by-side (blind.mp4) and KEY.txt.
Stops before cap_usd (models.json). Reads RUNPOD_API_KEY from .env; never prints it.
Spends money: run only with the owner's yes and a cap.
"""
import argparse, json, os, random, subprocess, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CFG = json.loads((Path(__file__).parent / "models.json").read_text(encoding="utf-8"))


def key():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("RUNPOD_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("RUNPOD_API_KEY missing in .env")


H = {"Authorization": "Bearer " + key(), "Content-Type": "application/json", "User-Agent": "projectv-ab/1.0"}


def call(ep, inp, timeout_s=900):
    req = urllib.request.Request(f"https://api.runpod.ai/v2/{ep}/run", data=json.dumps({"input": inp}).encode(), headers=H)
    try:
        jid = json.load(urllib.request.urlopen(req, timeout=60))["id"]
    except Exception as e:  # submit errors (bad params) are not billed
        return {"status": "FAILED", "error": f"{e} {getattr(e, 'read', lambda: b'')().decode(errors='ignore')[:300]}"}
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(8)
        try:
            s = json.load(urllib.request.urlopen(urllib.request.Request(f"https://api.runpod.ai/v2/{ep}/status/{jid}", headers=H), timeout=60))
        except Exception:
            continue
        if s.get("status") in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
            s["_secs"] = round(time.time() - t0)
            return s
    return {"status": "TIMEOUT", "id": jid}


def save(url, path):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "projectv-ab/1.0"}), timeout=300) as r:
        path.write_bytes(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--image-prompt")
    ap.add_argument("--image-url")
    ap.add_argument("--motion", required=True)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    log = (out / "log.jsonl").open("a", encoding="utf-8")
    spent, cap = 0.0, float(CFG["cap_usd"])
    img = a.image_url
    if not img:
        r = call(CFG["start_image"]["endpoint"], {"prompt": a.image_prompt, **CFG["start_image"]["input"]})
        spent += float((r.get("output") or {}).get("cost") or 0)
        log.write(json.dumps({"tag": "start", **r}) + "\n")
        img = r["output"]["result"]; save(img, out / "start.png")
    only = set(filter(None, a.only.split(",")))
    done = []
    for m in CFG["models"]:
        if only and m["name"] not in only:
            continue
        est = m["usd_per_s"] * m["input"].get("duration", 5)
        if spent + est > cap:
            print(m["name"], "skipped: cap"); continue
        r = call(m["endpoint"], {"prompt": a.motion, "image": img, **m["input"]})
        o = r.get("output") if isinstance(r.get("output"), dict) else {}
        spent += float(o.get("cost") or 0)
        log.write(json.dumps({"tag": m["name"], "status": r.get("status"), "output": o, "error": r.get("error"), "secs": r.get("_secs"), "spent": round(spent, 4)}) + "\n"); log.flush()
        print(m["name"], r.get("status"), o.get("cost"), (r.get("error") or "")[:200], "total", round(spent, 3), flush=True)
        if o.get("result"):
            save(o["result"], out / f"{m['name']}.mp4"); done.append(m["name"])
    random.shuffle(done)
    if done:
        font = "fontfile='C\\:/Windows/Fonts/arialbd.ttf'"
        ins, fl = [], []
        for i, n in enumerate(done):
            ins += ["-i", str(out / f"{n}.mp4")]
            fl.append(f"[{i}]fps=24,scale=288:512,setsar=1,trim=0:4,setpts=PTS-STARTPTS,drawtext={font}:text='{i+1}':x=10:y=10:fontsize=34:fontcolor=white:box=1:boxcolor=black@0.6[v{i}]")
        stack = "".join(f"[v{i}]" for i in range(len(done))) + (f"hstack={len(done)}" if len(done) > 1 else "null")
        subprocess.run(["ffmpeg", "-v", "error", *ins, "-filter_complex", ";".join(fl) + ";" + stack, "-an", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", str(out / "blind.mp4"), "-y"], check=False)
        (out / "KEY.txt").write_text("\n".join(f"{i+1}={n}" for i, n in enumerate(done)) + f"\ntotal_usd={round(spent, 4)}\n", encoding="utf-8")
    print("DONE total", round(spent, 3))


if __name__ == "__main__":
    main()
