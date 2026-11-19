import json

from workers.bench.wan_fp8 import bench_wan_fp8 as b
from workers.bench.wan_fp8.export_dream_inputs import build_manifest, frames_of


def _manifest(tmp_path, **over):
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "shot01.png").write_bytes(b"x")
    clip = {"id": "shot01", "image": "images/shot01.png", "prompt": "slow tilt up", "seed": 280202160,
            "width": 480, "height": 832, "frames": 81, "fps": 16, **over}
    m = tmp_path / "manifest.json"
    m.write_text(json.dumps({"clips": [clip]}))
    return m


def test_expert_split_matches_diffusers_boundary():
    # 4 steps, shift 3, boundary 0.9: timesteps 1000, 900.1, 750, 500 -> two high-noise steps
    assert b.expert_split(4, 3.0) == 2


def test_dry_run_ok(tmp_path, capsys):
    assert b.main(["--manifest", str(_manifest(tmp_path)), "--dry-run"]) == 0
    assert "dry run OK" in capsys.readouterr().out


def test_dry_run_rejects_bad_clip(tmp_path):
    assert b.main(["--manifest", str(_manifest(tmp_path, frames=80, width=500)), "--dry-run"]) == 2


def test_workflow_uses_fp8_experts_loras_and_split(tmp_path):
    class A:
        steps, shift, sampler, text_encoder = 4, 3.0, "uni_pc_bh2", "fp16"
    clip = {"id": "shot01", "prompt": "p", "seed": 7, "width": 480, "height": 832, "frames": 81}
    g = b.workflow(clip, "shot01.png", A)
    assert g["1"]["inputs"]["unet_name"].endswith("fp8_scaled.safetensors")
    assert g["3"]["inputs"]["lora_name"] == b.LORA_HIGH and g["4"]["inputs"]["lora_name"] == b.LORA_LOW
    assert g["13"]["inputs"]["end_at_step"] == 2 and g["14"]["inputs"]["start_at_step"] == 2
    assert g["13"]["inputs"]["cfg"] == 1.0 and g["13"]["inputs"]["noise_seed"] == 7


def test_download_size_is_about_42_gb_with_fp16_text_encoder():
    gb = sum(b.FILES[n][3] for n in b.needed_files("fp16")) / 1e9
    assert 41 < gb < 42


def test_export_manifest_uses_clip_asset_prompt_and_seed():
    dream = {"id": "d", "title": "T", "quality": "480p", "shots": [
        {"id": "s0", "ord": 0, "picture_asset_id": "p0", "clip_asset_id": "c0"},
        {"id": "s1", "ord": 1, "picture_asset_id": "p1", "clip_asset_id": None}]}
    assets = {"c0": {"prompt": "rendered prompt", "seed": 5, "width": 480, "height": 832, "duration_sec": 5.063,
                     "model": "wan2.2-i2v-a14b fast"}}
    m = build_manifest(dream, assets)
    assert len(m["clips"]) == 1
    c = m["clips"][0]
    assert (c["prompt"], c["seed"], c["frames"], c["baseline_asset_id"]) == ("rendered prompt", 5, 81, "c0")
    assert frames_of(None) == 81


def test_pack_bundle_and_exec_chunks_round_trip(tmp_path):
    import base64
    import tarfile

    from workers.bench.wan_fp8 import pack

    inp = tmp_path / "in"
    (inp / "images").mkdir(parents=True)
    (inp / "images" / "shot01.png").write_bytes(bytes(range(256)) * 40)
    (inp / "manifest.json").write_text(json.dumps({"clips": [
        {"id": "shot01", "image": "images/shot01.png", "prompt": "p", "seed": 1, "width": 480, "height": 832}]}))
    bundle = pack.pack(inp, tmp_path / "out")
    calls = [json.loads(p.read_text()) for p in sorted((tmp_path / "out" / "exec_chunks").glob("*.json"))]
    assert all(len(c) <= 256 and max(map(len, c)) <= 4096 for c in calls)
    assert base64.b64decode("".join(a for c in calls for a in c[4:])) == bundle.read_bytes()
    with tarfile.open(bundle) as t:
        names = t.getnames()
        assert t.extractfile("bench_in/images/shot01.png").read() == bytes(range(256)) * 40
    assert "bench_in/bench_wan_fp8.py" in names and "bench_in/manifest.json" in names
