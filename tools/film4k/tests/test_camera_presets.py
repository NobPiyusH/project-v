"""camera_presets, camera_graphs, repair and podkit.groups: pure functions only (no ffmpeg run, no pod)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "film4k"))
sys.path.insert(0, str(ROOT))
import camera_graphs as G  # noqa: E402
import camera_presets as P  # noqa: E402
import repair as R  # noqa: E402
from podkit import groups  # noqa: E402


def links(g):
    """Every [node_id, idx] input must point at a node that exists in the graph."""
    bad = []
    for nid, n in g.items():
        for k, v in n["inputs"].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] not in g:
                bad.append((nid, k, v))
    return bad


def test_every_fun_camera_preset_uses_a_real_pose_and_the_names_are_unique():
    for p in P.PRESETS.values():
        if p.engine == "fun_camera":
            assert p.pose in P.FUN_CAMERA_POSES and p.speed > 0
        else:
            assert p.path in P.PATHS
    assert len(P.PRESETS) == len({p.name for p in P.PRESETS.values()})


def test_unknown_preset_names_list_the_valid_ones():
    with pytest.raises(KeyError, match="zoom_in"):
        P.preset("whoosh")


def test_plan_is_varied_deterministic_and_never_repeats_or_rolls():
    for scene in range(1, 16):
        plan = P.plan_camera(scene, 3)
        assert plan == P.plan_camera(scene, 3)
        assert all(a != b for a, b in zip(plan, plan[1:], strict=False))
        assert plan[0] != plan[-1] and not (set(plan) & P.RARE)
    assert len({tuple(P.plan_camera(s, 3)) for s in range(1, 16)}) > 3


def test_plan_hints_win_and_are_checked():
    assert P.plan_camera(1, 3, "pan_left") == ["pan_left"] * 3
    assert P.plan_camera(1, 2, ["zoom_in", "drift_right"]) == ["zoom_in", "drift_right"]
    with pytest.raises(ValueError):
        P.plan_camera(1, 3, ["zoom_in"])
    assert set(P.plan_camera(4, 5, allow_untested=False)) <= P.TESTED


def test_vace_path_filters_only_grow_and_pull_back_reverses():
    for name in P.PATHS:
        f = P.vace_path_filter(name, 480, 832, 33)
        assert "eval=frame" in f and "edgedetect" in f and f.endswith("format=yuv420p")
        assert "(1.0-" not in f
    assert ",reverse," in P.vace_path_filter("pull_back", 480, 832, 33)
    assert "reverse" not in P.vace_path_filter("push_in", 480, 832, 33)
    with pytest.raises(KeyError):
        P.vace_path_filter("spin", 480, 832, 33)


def test_control_video_and_last_frame_commands():
    c = P.control_video_cmd("k.png", "c.mp4", "push_in", 480, 832, 33)
    assert c.index("-t") < c.index("-i")  # input is cut first, or reverse waits forever
    assert c[0] == "ffmpeg" and "-loop" in c and c[c.index("-frames:v") + 1] == "33" and c[-1] == "c.mp4"
    assert P.last_frame_cmd("b1.mp4", "l.png")[-1] == "l.png"


def test_fun_camera_graph_wires_up_and_uses_the_i2v_loras():
    g = G.fun_camera_graph({}, "pan_left", "kf.png", "walk forward", "s1_b1")
    assert not links(g)
    assert g["20"]["inputs"]["camera_pose"] == "Pan Left" and g["20"]["inputs"]["speed"] == 1.0
    assert g["1"]["inputs"]["unet_name"] == G.CAMERA_UNETS[0] and g["3"]["inputs"]["lora_name"] == G.I2V_LORAS[0]
    assert g["13"]["inputs"]["end_at_step"] == 2 and g["14"]["inputs"]["start_at_step"] == 2
    assert g["16"]["inputs"]["filename_prefix"] == "video/s1_b1"
    assert G.fun_camera_graph({}, "fast_push", "kf.png", "x", "p")["20"]["inputs"]["speed"] == 1.8


def test_vace_graphs_use_the_t2v_loras_and_trim_the_reference_frames():
    g = G.vace_graph({}, "ctrl.mp4", "kf.png", "walk", "p")
    assert not links(g) and g["3"]["inputs"]["lora_name"] == G.T2V_LORAS[0]
    assert g["22"]["inputs"]["trim_amount"] == ["12", 3] and "control_masks" not in g["12"]["inputs"]
    r = G.vace_repair_graph({}, "ctrl.mp4", "mask.mp4", "ref.png", "wing", "p")
    assert not links(r) and r["12"]["inputs"]["control_masks"] == ["25", 0] and r["25"]["inputs"]["channel"] == "red"


def test_graph_sizes_are_checked_and_missing_inputs_come_from_object_info():
    for w, h, n in ((481, 832, 33), (480, 832, 32), (480, 833, 33), (480, 832, 3)):
        with pytest.raises(ValueError):
            G.check_size(w, h, n)
    info = {"WanCameraEmbedding": {"input": {"required": {"camera_pose": [["Static"], {}], "fx": ["FLOAT", {"default": 0.5}]}}}}
    n = G.node("WanCameraEmbedding", info, camera_pose="Zoom In")
    assert n["inputs"] == {"camera_pose": "Zoom In", "fx": 0.5}
    with pytest.raises(ValueError):
        G.fun_camera_graph({}, "push_in", "kf.png", "x", "p")  # a VACE preset is not a Fun Camera preset


def test_repair_picks_inpaint_for_small_short_defects_and_reroll_otherwise():
    assert R.choose_mode(R.Region(0.1, 0.1, 0.3, 0.2, 0.7, 2.7)) == "vace_inpaint"
    assert R.choose_mode(R.Region(0.0, 0.0, 0.9, 0.9, 0.7, 2.0)) == "reroll"
    assert R.choose_mode(R.Region(0.1, 0.1, 0.2, 0.2, 0.0, 6.0)) == "reroll"
    with pytest.raises(ValueError):
        R.Region(0.9, 0.9, 0.3, 0.3, 0, 1).validate()


def test_window_frames_are_4k_plus_1_and_stay_inside_the_clip():
    for t0, t1 in ((0.6, 2.7), (0.0, 0.5), (3.5, 4.9), (1.0, 1.2)):
        start, n = R.window_frames(R.Region(0.1, 0.1, 0.2, 0.2, t0, t1), 16, 81)
        assert (n - 1) % 4 == 0 and n >= 5 and start >= 0 and start + n <= 81


def test_repair_commands_cover_the_same_frames_and_splice_around_them():
    reg = R.Region(0.2, 0.1, 0.3, 0.2, 0.7, 2.7)
    start, n = R.window_frames(reg, 16, 81)
    ctrl, mask = R.control_and_mask_cmds("clip.mp4", reg, 16, 81, 480, 832, "c.mp4", "m.mp4")
    assert ctrl[ctrl.index("-frames:v") + 1] == str(n) == mask[mask.index("-frames:v") + 1]
    assert f"between(n,{start},{start + n - 1})" in " ".join(ctrl) and "drawbox" in " ".join(mask)
    comp = " ".join(R.composite_cmd("clip.mp4", "rep.mp4", reg, 16, 81, 1080, 1920, "seg.mp4"))
    assert "maskedmerge" in comp and "gblur" in comp and "1080x1920" in comp
    sp = " ".join(R.splice_cmd("clip.mp4", "seg.mp4", reg, 16, 81, "out.mp4"))
    assert f"lt(n,{start})" in sp and f"gte(n,{start + n})" in sp and "concat=n=3" in sp


def test_model_groups_download_only_what_a_job_needs():
    base = groups.total_gb([])
    cam, vace = groups.total_gb(["fun_camera"]), groups.total_gb(["vace"])
    assert base < 8 and 38 < cam < 42 and 42 < vace < 47  # each includes the 7 GB text encoder and VAE
    both = groups.files_for(["fun_camera", "vace", "seedvr2"])
    assert sum(v[3] for v in both.values()) / 1e9 > 80 and "wan_2.1_vae.safetensors" in both
    assert groups.boot_minutes(["fun_camera"]) < groups.boot_minutes(["fun_camera", "vace", "seedvr2"])
    with pytest.raises(KeyError):
        groups.files_for(["nope"])
