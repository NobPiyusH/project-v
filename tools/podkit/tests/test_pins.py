"""Pins and sha256 verification (no network, no pod)."""

import base64
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from podkit import boot, groups, verify  # noqa: E402


def _all_files():
    out = dict(groups.COMMON)
    for g in groups.GROUPS.values():
        out.update(g)
    return out


def test_every_model_file_has_a_sha256_and_a_pinned_revision():
    for name, (repo, _path, _sub, _size) in _all_files().items():
        sha = groups.SHA256.get(name, "")
        assert len(sha) == 64 and int(sha, 16) >= 0, f"{name} has no sha256"
        rev = groups.REVISIONS.get(repo, "")
        assert len(rev) == 40 and int(rev, 16) >= 0, f"{repo} has no commit pin"


def test_pins_have_no_orphans():
    names = set(_all_files()) | {groups.ESRGAN[1]}
    assert set(groups.SHA256) == names
    assert set(groups.REVISIONS) == {v[0] for v in _all_files().values()} | {groups.ESRGAN[0]}


def test_pins_for_covers_every_group_file():
    pins = groups.pins_for(groups.files_for(list(groups.GROUPS)))
    assert set(pins) == set(groups.files_for(list(groups.GROUPS)))
    assert groups.pins_for({"made_up.bin": ["x/y", "p", "s", 1]}) == {}


def test_images_are_pinned_by_digest():
    assert groups.pinned_image("runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404").startswith("runpod/pytorch@sha256:")
    assert groups.pinned_image("ghcr.io/x/y:z") == "ghcr.io/x/y:z"
    assert all(d.startswith("sha256:") and len(d) == 71 for d in groups.IMAGES.values())


def test_verify_file_accepts_the_right_bytes(tmp_path):
    f = tmp_path / "w.bin"
    f.write_bytes(b"weights" * 1000)
    sha = hashlib.sha256(f.read_bytes()).hexdigest()
    assert verify.verify_file(str(f), sha, 7000) == (True, "ok")
    assert verify.verify_file(str(f), sha.upper())[0] is True


def test_verify_file_refuses_wrong_missing_truncated_and_unpinned(tmp_path):
    f = tmp_path / "w.bin"
    f.write_bytes(b"abc")
    sha = hashlib.sha256(b"abc").hexdigest()
    assert not verify.verify_file(str(f), "0" * 64)[0]
    ok, why = verify.verify_file(str(f), sha, 99)
    assert not ok and "size" in why
    assert verify.verify_file(str(tmp_path / "nope"), sha) == (False, "missing")
    assert not verify.verify_file(str(f), "")[0]


def test_boot_ships_the_pins_and_the_verifier_and_downloads_at_the_pinned_revision():
    files = {"wan_2.1_vae.safetensors": list(groups.COMMON["wan_2.1_vae.safetensors"])}
    env = {e["key"]: e["value"] for e in boot.pod_env("tok", "https://kj", files)}
    pins = json.loads(base64.b64decode(env["PINS_B64"]))
    assert pins["wan_2.1_vae.safetensors"]["sha256"] == groups.SHA256["wan_2.1_vae.safetensors"]
    assert "def verify_file" in base64.b64decode(env["VERIFY_B64"]).decode()
    assert "revision=" in boot.DL and "verify.verify_file" in boot.DL and "SHA MISMATCH" in boot.DL
    script = boot.boot_script()
    assert "refs/heads/master" not in script and "resolve/main" not in script
    assert "sha256sum -c" in script and groups.SHA256[groups.ESRGAN[1]] in script


def test_the_pod_download_code_really_rejects_a_bad_file(tmp_path, monkeypatch):
    """Runs boot.DL itself with a fake hf_hub_download that returns wrong bytes: it must delete them and print FAILED."""
    import types

    root = tmp_path / "ws"
    (root / "dl").mkdir(parents=True)
    bad = root / "dl" / "w.bin"
    pinned = hashlib.sha256(b"good").hexdigest()
    (root / "files.json").write_text(json.dumps({"w.bin": ["r/x", "w.bin", "vae", 4]}))
    (root / "pins.json").write_text(json.dumps({"w.bin": {"sha256": pinned, "revision": "a" * 40}}))
    (root / "verify.py").write_text((Path(boot.__file__).parent / "verify.py").read_text(encoding="utf-8"))
    calls = []

    def fake(repo, path, local_dir, token, revision):
        calls.append(revision)
        bad.write_bytes(b"evil")
        return str(bad)

    hub = types.ModuleType("huggingface_hub")
    hub.hf_hub_download = fake
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.delitem(sys.modules, "verify", raising=False)
    code = boot.DL.replace("/workspace", str(root).replace("\\", "/")).replace("time.sleep(5)", "pass")
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(code, "dl.py", "exec"), {"__name__": "dl"})
    out = buf.getvalue()
    assert "SHA MISMATCH w.bin" in out and "FAILED w.bin" in out and "SHA OK" not in out
    assert calls == ["a" * 40] * 3 and not bad.exists()
    assert not (root / "ComfyUI-master" / "models" / "vae" / "w.bin").exists()
    sys.modules.pop("verify", None)
