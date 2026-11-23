"""Crash-proof cloud Blender renderer (RunPod). Renders in short chunks, pulls every finished frame to the laptop at once,
detects a dead pod, rents a new one and resumes from the first missing frame, ALWAYS terminates pods at the end,
and arms an independent kill-timer for every pod.

usage:
  python cloud_render.py --scene a --frames 1-24 --width 2160 --height 3840 --engine CYCLES --samples 64 --chunk 6 --out DIR
  options: --kill-after-chunks N  (test: really terminates the pod after N chunks, to prove resume)
           --backend local        (free logic test: runs Blender on this laptop; --simulate-death-after N)
           --max-minutes M        (hard stop for the whole job, default 40)  --max-price P ($/h ceiling, default 1.6)
Secrets: RUNPOD_API_KEY from C:\\dev\\project-v\\.env (never printed). Cost log: <ROOT>/cost_log.md
"""
import argparse, base64, json, os, re, secrets, subprocess, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("MS_ROOT", r"E:\project-data\blender\milky-seas")
SCENE_SCRIPTS = os.path.join(HERE, "..", "blender", "milky_seas")
ENV_FILE = os.environ.get("PROJECTV_ENV", r"C:\dev\project-v\.env")
MW_URL = "https://svs.gsfc.nasa.gov/vis/a000000/a004800/a004851/milkyway_2020_8k.exr"
SCENES = {
    "a": {"blend": "scenes/a_earth.blend", "files": ["textures/earth_base_21600.jpg", "textures/lights_4096.jpg"], "remote": {"textures/milkyway_2020_8k.exr": MW_URL}},
    "b": {"blend": "scenes/b_ship.blend", "files": [], "remote": {"textures/milkyway_2020_8k.exr": MW_URL}},
    "c": {"blend": "scenes/c_macro.blend", "files": [], "remote": {}},
}
GPUS = ["NVIDIA L40S", "NVIDIA RTX 6000 Ada Generation", "NVIDIA L40", "NVIDIA A40", "NVIDIA RTX A6000"]
IMAGE = "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04"
BLENDER_URL = "https://download.blender.org/release/Blender5.3/blender-5.3.0-linux-x64.tar.xz"
UA = {"User-Agent": "projectv-render"}


class PodDead(Exception):
    pass


def say(m):
    print(time.strftime("%H:%M:%S ") + m, flush=True)


def read_key():
    for line in open(ENV_FILE, encoding="utf-8"):
        m = re.match(r"^RUNPOD_API_KEY=(.*)$", line.strip())
        if m:
            return m.group(1).strip().strip('"')
    sys.exit("RUNPOD_API_KEY missing in .env")


def png_ok(b):
    return len(b) > 2000 and b[:8] == b"\x89PNG\r\n\x1a\n" and b[-12:-8] == b"\x00\x00\x00\x00" and b[-8:-4] == b"IEND"


def cost_log(line):
    open(os.path.join(ROOT, "cost_log.md"), "a", encoding="utf-8").write(line + "\n")


# ----------------------------------------------------------------------------------------------------------------
class RunPodBackend:
    BOOT = r'''#!/bin/bash
mkdir -p /workspace/logs /workspace/ms/out
echo "$SRV_B64" | base64 -d > /workspace/srv.py && (nohup python3 /workspace/srv.py >/workspace/logs/srv.log 2>&1 &)
exec > >(tee -a /workspace/logs/boot.log) 2>&1
export DEBIAN_FRONTEND=noninteractive; T0=$(date +%s)
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
(nvidia-smi --query-gpu=timestamp,memory.used,utilization.gpu --format=csv,noheader -l 1 > /workspace/logs/gpu.csv 2>&1 &)
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq libegl1 libgl1 libxkbcommon0 libxi6 libxrender1 libsm6 libxxf86vm1 libxfixes3 libvulkan1 xz-utils bc >/dev/null 2>&1 &
echo "$REMOTE_B64" | base64 -d > /workspace/remote.txt
while read -r dst url; do [ -n "$dst" ] && mkdir -p /workspace/ms/$(dirname $dst) && (curl -sL -o /workspace/ms/$dst "$url" &) ; done < /workspace/remote.txt
curl -sL "$BLENDER_URL" -o /workspace/b.tar.xz && tar xf /workspace/b.tar.xz -C /workspace && mv /workspace/blender-5.3.0-linux-x64 /workspace/blender && rm /workspace/b.tar.xz
wait
sleep 2
echo "BOOT COMPLETE $(( $(date +%s)-T0 ))s"
touch /workspace/READY
sleep infinity
'''

    def __init__(self, job, args, state_dir):
        self.job, self.args = job, args
        self.key = read_key()
        self.state_path = os.path.join(state_dir, "pods.json")
        self.pod = None
        self.fail_since = None
        self.pods_used = []
        os.makedirs(state_dir, exist_ok=True)
        if not os.path.exists(self.state_path):
            json.dump({"released": []}, open(self.state_path, "w"))

    # --- RunPod API (key never printed) ---
    def gql(self, query, variables=None):
        req = urllib.request.Request("https://api.runpod.io/graphql?api_key=" + self.key, method="POST",
                                     data=json.dumps({"query": query, "variables": variables or {}}).encode(),
                                     headers={"Content-Type": "application/json", **UA})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            return {"errors": [{"message": "HTTP %s" % e.code}]}
        except Exception as e:  # noqa: BLE001
            return {"errors": [{"message": type(e).__name__}]}

    def api_status(self, pod_id):
        """'RUNNING' / 'EXITED' / 'GONE' / 'UNKNOWN' (API unreachable)."""
        r = self.gql("query($i: PodFilter){ pod(input:$i){ id desiredStatus } }", {"i": {"podId": pod_id}})
        if r.get("errors") and not r.get("data"):
            msg = str(r["errors"][0].get("message", ""))
            return "GONE" if "not found" in msg.lower() or "does not exist" in msg.lower() else "UNKNOWN"
        p = (r.get("data") or {}).get("pod")
        return p["desiredStatus"] if p else "GONE"

    def terminate(self, pod_id):
        for _ in range(4):
            r = self.gql("mutation($input: PodTerminateInput!){ podTerminate(input:$input) }", {"input": {"podId": pod_id}})
            if not r.get("errors") or "not found" in str(r["errors"]).lower():
                return True
            time.sleep(3)
        return False

    # --- lifecycle ---
    def rent(self):
        a = self.args
        token = secrets.token_hex(16)
        remote = "\n".join("%s %s" % (k, v) for k, v in SCENES[a.scene]["remote"].items()) + "\n"
        srv = open(os.path.join(HERE, "pod_server.py"), "rb").read()
        boot_b64 = base64.b64encode(self.BOOT.encode()).decode()
        env = [{"key": "EXEC_TOKEN", "value": token}, {"key": "SRV_B64", "value": base64.b64encode(srv).decode()},
               {"key": "REMOTE_B64", "value": base64.b64encode(remote.encode()).decode()}, {"key": "BLENDER_URL", "value": BLENDER_URL}]
        for cloud in ("SECURE", "COMMUNITY"):
            for g in GPUS:
                inp = {"cloudType": cloud, "gpuCount": 1, "gpuTypeId": g, "name": "projectv-blender-" + a.scene, "imageName": IMAGE,
                       "containerDiskInGb": 40, "volumeInGb": 0, "minVcpuCount": 4, "minMemoryInGb": 32, "ports": "8189/http",
                       "dockerArgs": 'bash -c "echo %s | base64 -d | bash"' % boot_b64, "env": env}
                r = self.gql("mutation($input: PodFindAndDeployOnDemandInput){ podFindAndDeployOnDemand(input:$input){ id costPerHr } }", {"input": inp})
                d = (r.get("data") or {}).get("podFindAndDeployOnDemand")
                if d and d.get("id"):
                    price = float(d.get("costPerHr") or 0)
                    if price > a.max_price:
                        self.terminate(d["id"]); say("price %.2f over ceiling, released %s" % (price, g)); continue
                    self.pod = {"id": d["id"], "gpu": g, "cloud": cloud, "price": price, "token": token, "created": time.time()}
                    self.pods_used.append(self.pod)
                    say("rented %s %s in %s at $%.2f/h (pod %s)" % (g, "", cloud, price, d["id"]))
                    # independent kill-timer, detached: outlives this script if it dies
                    subprocess.Popen([sys.executable, os.path.join(HERE, "killer.py"), d["id"], str(int(a.max_minutes * 60 + 300)), self.state_path],
                                     creationflags=0x00000008 | 0x00000200 if os.name == "nt" else 0, close_fds=True,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    cost_log("- %s rented pod `%s` %s %s $%.2f/h (job %s)" % (time.strftime("%Y-%m-%d %H:%M"), d["id"], g, cloud, price, self.job))
                    return
                say("  no %s in %s: %s" % (g, cloud, str((r.get("errors") or [{}])[0].get("message"))[:80]))
        raise RuntimeError("no GPU available under $%.2f/h" % a.max_price)

    def release(self):
        if not self.pod:
            return
        pid = self.pod["id"]
        ok = self.terminate(pid)
        mins = (time.time() - self.pod["created"]) / 60
        st = json.load(open(self.state_path))
        st["released"].append(pid); json.dump(st, open(self.state_path, "w"))
        cost = mins / 60 * self.pod["price"]
        cost_log("- %s terminated `%s` after %.1f min = ~$%.3f (%s)" % (time.strftime("%H:%M"), pid, mins, cost, "ok" if ok else "TERMINATE FAILED, killer will retry"))
        say("pod %s terminated (%.1f min, ~$%.3f)%s" % (pid, mins, cost, "" if ok else "  WARNING terminate failed; kill-timer will retry"))
        self.pod = None

    # --- pod I/O ---
    def _base(self):
        return "https://%s-8189.proxy.runpod.net" % self.pod["id"]

    def _req(self, method, path, data=None, timeout=60):
        req = urllib.request.Request(self._base() + path, data=data, method=method, headers=UA)
        try:
            r = urllib.request.urlopen(req, timeout=timeout).read()
            self.fail_since = None
            return r
        except urllib.error.HTTPError as e:
            if e.code in (404, 403) and not path.startswith("/get/"):
                pass
            self._failed(e)
            if path.startswith("/get/") and e.code == 404:
                return None
            raise
        except Exception as e:  # noqa: BLE001
            self._failed(e)
            raise

    def _failed(self, e):
        now = time.time()
        self.fail_since = self.fail_since or now

    def check_dead(self):
        """Call after an I/O error: decide whether the pod is gone."""
        if not self.fail_since:
            return
        dur = time.time() - self.fail_since
        if dur > 45:
            st = self.api_status(self.pod["id"])
            if st in ("GONE", "EXITED", "TERMINATED"):
                raise PodDead("API says pod is %s" % st)
            if dur > 300:
                raise PodDead("pod unreachable for %.0f s (API says %s)" % (dur, st))

    def sh(self, cmd, timeout=100):
        try:
            return self._req("POST", "/x/" + self.pod["token"], cmd.encode(), timeout).decode(errors="replace")
        except Exception:
            self.check_dead()
            raise

    def wait_ready(self, limit=12 * 60):
        t0 = time.time()
        while time.time() - t0 < limit:
            try:
                try:
                    self._req("GET", "/ready", timeout=15)
                except urllib.error.HTTPError as he:
                    if he.code == 503:
                        self.fail_since = None      # server is up, Blender still installing
                    raise
                say("pod ready after %.0f s: %s" % (time.time() - t0, self.sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader").strip()[-60:]))
                return
            except Exception:
                pass
            self.check_dead()
            if self.api_status(self.pod["id"]) in ("GONE", "EXITED"):
                raise PodDead("pod disappeared during boot")
            time.sleep(8)
        raise PodDead("pod not ready after %d s" % limit)

    def put_file(self, rel, local):
        size = os.path.getsize(local)
        try:
            have = int(self.sh("stat -c%%s /workspace/ms/%s 2>/dev/null || echo 0" % rel).split("]")[-1].strip() or 0)
        except ValueError:
            have = 0
        if have == size:
            return
        with open(local, "rb") as f:
            off = 0
            while off < size:
                part = f.read(24 * 1024 * 1024)
                for attempt in range(3):
                    try:
                        self._req("PUT", "/up/%s/%s?off=%d" % (self.pod["token"], rel, off), part, timeout=300)
                        break
                    except Exception:
                        self.check_dead()
                        if attempt == 2:
                            raise PodDead("upload failing")
                        time.sleep(3)
                off += len(part)
        say("uploaded %s (%.1f MB)" % (rel, size / 1e6))

    def setup(self):
        self.rent()
        self.wait_ready()
        sc = SCENES[self.args.scene]
        for rel in [sc["blend"]] + sc["files"]:
            self.put_file(rel, os.path.join(ROOT, rel))
        self.put_file("render_job.py", os.path.join(SCENE_SCRIPTS, "render_job.py"))
        # remote files (the 8k star map): wait until the pod finished downloading them
        for rel in sc["remote"]:
            for _ in range(30):
                out = self.sh("stat -c%%s /workspace/ms/%s 2>/dev/null || echo 0" % rel).split("]")[-1].strip()
                if out.isdigit() and int(out) > 100_000_000:
                    break
                time.sleep(5)

    def start_chunk(self, a, b):
        j, ar = self.job, self.args
        cmd = ("cd /workspace/ms && mkdir -p out/%s logs && (nohup bash -c '/workspace/blender/blender -b %s --gpu-backend vulkan -P render_job.py -- "
               "--engine %s --width %d --height %d --samples %d --frames %d-%d --out out/%s %s --denoiser %s %s; echo $? > logs/chunk_%d_%d.rc' "
               "> logs/chunk_%d_%d.log 2>&1 &)") % (j, SCENES[ar.scene]["blend"], ar.engine, ar.width, ar.height, ar.samples, a, b, j,
                                                    "--gpu --device OPTIX" if ar.engine.upper() == "CYCLES" else "", ar.denoiser, "", a, b, a, b)
        self.sh(cmd)

    def poll(self, a, b):
        """-> (done_frames{frame: seconds,vram}, finished, rc)"""
        out = self.sh("cd /workspace/ms; for f in out/%s/frame_*.json; do [ -f $f ] && cat $f && echo; done; echo ---; cat logs/chunk_%d_%d.rc 2>/dev/null" % (self.job, a, b))
        body = out.split("\n", 1)[-1]
        frames, _, rc = body.partition("---")
        done = {}
        for ln in frames.splitlines():
            try:
                d = json.loads(ln); done[d["frame"]] = d
            except Exception:
                pass
        return done, rc.strip() != "", rc.strip()

    def fetch(self, frame):
        for _ in range(3):
            b = self._req("GET", "/get/%s/out/%s/frame_%04d.png" % (self.pod["token"], self.job, frame), timeout=300)
            if b and png_ok(b):
                return b
            time.sleep(2)
        raise RuntimeError("frame %d failed to download intact" % frame)

    def gpu_peak_mb(self):
        try:
            out = self.sh("cut -d, -f2 /workspace/logs/gpu.csv | tr -d ' MiB' | sort -n | tail -1").split("]")[-1].strip()
            return int(out)
        except Exception:
            return None

    def simulate_death(self):
        say("TEST: terminating the pod on purpose to prove resume")
        self.terminate(self.pod["id"])


# ----------------------------------------------------------------------------------------------------------------
class LocalBackend:
    """Free logic test: renders with the laptop's Blender (EEVEE, low res). Same interface; death is simulated."""
    def __init__(self, job, args, state_dir):
        self.job, self.args = job, args
        self.dir = os.path.join(ROOT, "work", "local_" + job)
        self.pod = None; self.pods_used = []; self.n_setup = 0; self.proc = None

    def setup(self):
        self.n_setup += 1
        self.pod = {"id": "local%d" % self.n_setup}; self.pods_used.append(self.pod)
        say("local 'pod' %s up" % self.pod["id"])

    def release(self):
        self.pod = None

    def start_chunk(self, a, b):
        ar = self.args
        blend = os.path.join(ROOT, SCENES[ar.scene]["blend"].replace(".blend", "_preview.blend"))
        self.proc = subprocess.Popen(["E:/Blender/blender.exe", "-b", blend, "-P", os.path.join(SCENE_SCRIPTS, "render_job.py"), "--", "--engine", "EEVEE", "--width", str(ar.width),
                                      "--height", str(ar.height), "--samples", "8", "--frames", "%d-%d" % (a, b), "--out", self.dir, "--depth", "8"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def poll(self, a, b):
        done = {}
        for f in range(a, b + 1):
            p = os.path.join(self.dir, "frame_%04d.json" % f)
            if os.path.exists(p):
                done[f] = json.load(open(p))
        fin = self.proc.poll() is not None
        return done, fin, str(self.proc.returncode) if fin else ""

    def fetch(self, frame):
        return open(os.path.join(self.dir, "frame_%04d.png" % frame), "rb").read()

    def gpu_peak_mb(self):
        return None

    def simulate_death(self):
        say("TEST: simulated pod death")
        if self.proc: self.proc.kill()
        for f in os.listdir(self.dir):                 # the dead 'pod' loses its disk
            os.remove(os.path.join(self.dir, f))
        raise PodDead("simulated")


# ----------------------------------------------------------------------------------------------------------------
def runs(missing, chunk):
    out, cur = [], []
    for f in sorted(missing):
        if cur and (f != cur[-1] + 1 or len(cur) >= chunk):
            out.append((cur[0], cur[-1])); cur = []
        cur.append(f)
    if cur:
        out.append((cur[0], cur[-1]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True, choices=list(SCENES)); ap.add_argument("--frames", default="1-1")
    ap.add_argument("--width", type=int, default=2160); ap.add_argument("--height", type=int, default=3840)
    ap.add_argument("--engine", default="CYCLES"); ap.add_argument("--samples", type=int, default=64)
    ap.add_argument("--denoiser", default="OPTIX"); ap.add_argument("--chunk", type=int, default=6)
    ap.add_argument("--out", required=True); ap.add_argument("--job", default=None)
    ap.add_argument("--backend", default="runpod", choices=["runpod", "local"])
    ap.add_argument("--kill-after-chunks", type=int, default=0); ap.add_argument("--simulate-death-after", type=int, default=0)
    ap.add_argument("--max-minutes", type=float, default=40); ap.add_argument("--max-price", type=float, default=1.6)
    ap.add_argument("--max-pods", type=int, default=4)
    args = ap.parse_args()
    f0, f1 = [int(x) for x in args.frames.split("-")]
    job = args.job or "%s_%dx%d_%s" % (args.scene, args.width, args.height, args.engine.lower())
    os.makedirs(args.out, exist_ok=True)
    state_dir = os.path.join(ROOT, "work", "pods")
    be = (RunPodBackend if args.backend == "runpod" else LocalBackend)(job, args, state_dir)
    deadline = time.time() + args.max_minutes * 60
    stats = {}
    chunks_pulled = 0
    pods = 0

    def have():
        return {f for f in range(f0, f1 + 1) if os.path.exists(os.path.join(args.out, "frame_%04d.png" % f))}

    try:
        while have() != set(range(f0, f1 + 1)):
            if time.time() > deadline:
                say("max minutes reached, stopping with %d/%d frames" % (len(have()), f1 - f0 + 1)); break
            if pods >= args.max_pods:
                say("too many pod replacements, stopping"); break
            try:
                pods += 1
                be.setup()
                for (a, b) in runs(set(range(f0, f1 + 1)) - have(), args.chunk):
                    be.start_chunk(a, b)
                    t0 = time.time()
                    pulled = set()
                    while True:
                        if time.time() > deadline:
                            raise TimeoutError("deadline")
                        time.sleep(3)
                        try:
                            done, fin, rc = be.poll(a, b)
                        except PodDead:
                            raise
                        except Exception:
                            continue                         # transient; check_dead() raises PodDead when it is really gone
                        for f, d in sorted(done.items()):
                            if f in pulled or not (a <= f <= b):
                                continue
                            png = be.fetch(f)
                            tmp = os.path.join(args.out, "frame_%04d.png.part" % f)
                            open(tmp, "wb").write(png)
                            os.replace(tmp, os.path.join(args.out, "frame_%04d.png" % f))      # pulled to the laptop at once
                            open(os.path.join(args.out, "frame_%04d.json" % f), "w").write(json.dumps(d))
                            stats[f] = d; pulled.add(f)
                            say("frame %d pulled (%.1f s on pod, %d KB)" % (f, d["seconds"], len(png) // 1024))
                        if fin and pulled >= set(range(a, b + 1)):
                            break
                        if fin and rc not in ("0", ""):
                            raise RuntimeError("blender exited with %s for chunk %d-%d" % (rc, a, b))
                    chunks_pulled += 1
                    say("chunk %d-%d done in %.0f s" % (a, b, time.time() - t0))
                    if args.kill_after_chunks and chunks_pulled == args.kill_after_chunks and pods == 1:
                        be.simulate_death()                       # real runs: terminates the pod; next poll detects the death
                    if args.simulate_death_after and chunks_pulled == args.simulate_death_after and pods == 1:
                        be.simulate_death()
            except PodDead as e:
                say("POD DEAD (%s): renting a new one and resuming from the first missing frame (%d of %d frames safe on the laptop)" % (e, len(have()), f1 - f0 + 1))
                cost_log("- %s pod died / was lost: %s; resumed with %d frames already pulled" % (time.strftime("%H:%M"), e, len(have())))
                be.release()
                continue
            except TimeoutError:
                break
    finally:
        peak = None
        try:
            if be.pod and args.backend == "runpod":
                peak = be.gpu_peak_mb()
        except Exception:
            pass
        be.release()
        if stats:
            secs = [d["seconds"] for d in stats.values()]
            summ = {"job": job, "frames_done": len(have()), "frames_total": f1 - f0 + 1, "pods_used": len(be.pods_used), "gpu_peak_mb": peak,
                    "seconds_per_frame_mean": round(sum(secs) / len(secs), 2), "seconds_per_frame_min": min(secs), "seconds_per_frame_max": max(secs),
                    "vram_after_frame_mb": max((d.get("vram_mb") or 0) for d in stats.values())}
            json.dump(summ, open(os.path.join(args.out, "summary.json"), "w"), indent=1)
            say("SUMMARY " + json.dumps(summ))


if __name__ == "__main__":
    main()
