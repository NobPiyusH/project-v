"""JarvisLabs helper for the control plane's JarvislabsProvider: one command per call, JSON on stdout.

JarvisLabs documents no REST API for instances, only the `jarvislabs` Python SDK (docs.jarvislabs.ai/sdk, 22 Nov 2026), so the Java
adapter shells out to this file. The API key comes from the JL_API_KEY environment variable (never argv, never printed). Run with:
    uv run --with jarvislabs python workers/jarvislabs_helper.py <command>
Commands (the request, when there is one, is JSON on stdin):
    gpus                      -> [{gpu_type, region, free, price_per_hour, vram, workload_type}]
    list                      -> [instance...]
    get <id>                  -> instance
    create  {name, gpu_type, region, storage, template, http_ports, script}  -> instance (blocks until Running)
    destroy <id>              -> {"destroyed": true}
Errors: exit 1 and {"error": "...", "type": "NotFoundError"|...} on stdout. NOT verified against the live API: no key was used when
this was written; the field names follow the SDK docs and source (client.py, instances.py) read on 22 Nov 2026.
"""
from __future__ import annotations

import json
import sys

SCRIPT_PREFIX = "projectv-"


def inst(i) -> dict:
    d = i.model_dump() if hasattr(i, "model_dump") else dict(i.__dict__)
    keys = ("machine_id", "name", "status", "gpu_type", "num_gpus", "cost", "public_ip", "url", "endpoints", "http_ports", "region")
    return {k: d.get(k) for k in keys}


def drop_scripts(c, prefix: str = SCRIPT_PREFIX) -> None:
    """Startup scripts hold the worker's boot payload and token: remove ours once the instance is gone."""
    try:
        for s in c.scripts.list():
            if str(getattr(s, "script_name", "")).startswith(prefix):
                c.scripts.remove(s.script_id)
    except Exception:  # noqa: BLE001 - cleanup only
        pass


def main(argv: list[str]) -> dict | list:
    from jarvislabs import Client

    cmd = argv[1] if len(argv) > 1 else ""
    with Client() as c:  # key from JL_API_KEY
        if cmd == "gpus":
            out = []
            for g in c.account.gpu_availability():
                d = g.model_dump()
                out.append({"gpu_type": d.get("gpu_type"), "region": d.get("region"), "free": d.get("num_free_devices"),
                            "price_per_hour": d.get("price_per_hour"), "vram": d.get("vram"), "workload_type": d.get("workload_type")})
            return out
        if cmd == "list":
            return [inst(i) for i in c.instances.list()]
        if cmd == "get":
            return inst(c.instances.get(int(argv[2])))
        if cmd == "destroy":
            c.instances.destroy(int(argv[2]))
            drop_scripts(c)
            return {"destroyed": True}
        if cmd == "create":
            req = json.load(sys.stdin)
            name = req["name"]
            if not name.startswith(SCRIPT_PREFIX):
                raise ValueError("instance name must start with " + SCRIPT_PREFIX)
            c.scripts.add(req["script"], name=name)  # returns True, not an id: look it up by name
            script_id = next((str(s.script_id) for s in c.scripts.list() if getattr(s, "script_name", None) == name), None)
            if script_id is None:
                raise RuntimeError("startup script was added but is not in scripts.list()")
            try:
                i = c.instances.create(gpu_type=req["gpu_type"], num_gpus=1, template=req.get("template", "pytorch"),
                                       storage=int(req["storage"]), name=name, region=req.get("region"),
                                       http_ports=req.get("http_ports", ""), script_id=script_id)
            except Exception:
                drop_scripts(c, name)
                raise
            return inst(i)
    raise ValueError("unknown command: " + cmd)


if __name__ == "__main__":
    try:
        print(json.dumps(main(sys.argv)))
    except Exception as e:  # noqa: BLE001 - one JSON error line for the Java side
        print(json.dumps({"error": str(e)[:300], "type": type(e).__name__}))
        sys.exit(1)
