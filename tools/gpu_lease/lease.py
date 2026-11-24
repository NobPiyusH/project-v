"""Watchdog lease for test pods (owner approved 23 Nov 2026).

The studio's GPU watchdog (control-plane `com.projectv.gpu.PodWatchdog`) terminates any pod/instance named `projectv-*` that has
no lease (after 10 min), a released lease, a heartbeat older than 15 min, or a runtime over 180 min. Test pods started by these
tools must therefore hold a lease, exactly like studio pods do. This keeps ALL watchdog protection on:
  - open: inserts the lease row in the studio database (table gpu_pod_lease)
  - keep: heartbeats every 60 s; if this process dies the watchdog kills the pod within about 15 min (dead-man switch)
  - release: ends the lease; the watchdog then terminates the pod on its next sweep (about 3 min), so a normal finish cleans up too

Usage:
  python lease.py keep <provider> <pod_id> <name> <purpose> [--state FILE] [--max-minutes 170]
      keep runs until FILE (a JSON with "released": true) says stop, or --max-minutes, or the pod row disappears; then releases.
  python lease.py open|heartbeat|release <provider> <pod_id> [name] [purpose]
Needs Docker running (talks to the studio Postgres through `docker exec projectv-postgres-1 psql`). No keys involved.
"""
import json, re, subprocess, sys, time

SAFE = re.compile(r"^[A-Za-z0-9_.-]{3,80}$")


def psql(sql):
    r = subprocess.run(["docker", "exec", "projectv-postgres-1", "psql", "-U", "projectv", "-d", "projectv", "-tA", "-c", sql],
                       capture_output=True, text=True, timeout=60, creationflags=0x08000000 if sys.platform == "win32" else 0)
    if r.returncode:
        raise RuntimeError("psql failed: " + r.stderr.strip()[:200])
    return r.stdout.strip()


def _check(*vals):
    for v in vals:
        if not SAFE.match(v or ""):
            raise ValueError(f"unsafe or empty value: {v!r}")


def open_lease(provider, pod_id, name, purpose):
    _check(provider, pod_id, name)
    purpose = re.sub(r"[^A-Za-z0-9 _.,:-]", "", purpose or "test")[:80]
    psql(f"INSERT INTO gpu_pod_lease (pod_id, provider, name, purpose) VALUES ('{pod_id}', '{provider}', '{name}', '{purpose}') "
         f"ON CONFLICT (pod_id) DO UPDATE SET heartbeat_at = now(), released_at = NULL")


def heartbeat(pod_id):
    _check(pod_id)
    psql(f"UPDATE gpu_pod_lease SET heartbeat_at = now() WHERE pod_id = '{pod_id}' AND released_at IS NULL")


def release(pod_id):
    _check(pod_id)
    psql(f"UPDATE gpu_pod_lease SET released_at = now() WHERE pod_id = '{pod_id}' AND released_at IS NULL")


def keep(provider, pod_id, name, purpose, state=None, max_minutes=170, tries=6, wait_s=10):
    """Open the lease (retrying: Docker may still be starting), then heartbeat. If the lease cannot be opened at all, say so
    LOUDLY and exit 2: a pod with no lease is protected by its kill-timer only (24 Nov 2026: Docker was off and this died silently)."""
    for attempt in range(1, tries + 1):
        try:
            open_lease(provider, pod_id, name, purpose)
            break
        except Exception as e:  # noqa: BLE001
            print(f"lease open failed ({attempt}/{tries}): {e}", file=sys.stderr, flush=True)
            if attempt == tries:
                print("NO LEASE: the studio watchdog does not protect this pod; only the kill-timer does", file=sys.stderr, flush=True)
                sys.exit(2)
            time.sleep(wait_s)
    end = time.time() + max_minutes * 60
    misses = 0
    while time.time() < end:
        time.sleep(60)
        try:
            if state and json.load(open(state)).get("released"):
                break
        except Exception:
            pass
        try:
            heartbeat(pod_id); misses = 0
        except Exception:
            misses += 1
            if misses >= 3:
                return  # database unreachable: stop; the watchdog's dead-man rule applies
    try:
        release(pod_id)
    except Exception:
        pass


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    cmd = a[0]
    if cmd == "keep":
        opts = {}
        rest = []
        i = 1
        while i < len(a):
            if a[i] in ("--state", "--max-minutes"):
                opts[a[i]] = a[i + 1]; i += 2
            else:
                rest.append(a[i]); i += 1
        keep(rest[0], rest[1], rest[2], rest[3] if len(rest) > 3 else "test", opts.get("--state"), int(opts.get("--max-minutes", 170)))
    elif cmd == "ping":  # is the studio database (Docker) reachable? exit 0 = yes
        print("database ok" if psql("select 1") == "1" else "database NOT ok")
    elif cmd == "open":
        open_lease(a[1], a[2], a[3], a[4] if len(a) > 4 else "test"); print("lease opened")
    elif cmd == "heartbeat":
        heartbeat(a[2] if len(a) > 2 else a[1]); print("heartbeat ok")
    elif cmd == "release":
        release(a[2] if len(a) > 2 else a[1]); print("lease released")
    else:
        sys.exit(__doc__)
