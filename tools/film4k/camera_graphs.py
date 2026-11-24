"""ComfyUI API graphs for camera-controlled shots (pure functions; they build JSON, they run nothing).

Built from the graphs that WORKED on a real pod on 24 Nov 2026 (docs/pod-tests-24nov.md): Wan 2.2 Fun Camera (two fp8 experts, I2V 4-step
lightx2v LoRAs) and Wan 2.2 Fun VACE (two fp8 experts, T2V lightx2v LoRAs), both with the proven 4-step 2+2 expert split. Node inputs that a
caller does not name are filled from the pod's /object_info defaults (`info`), so a missing optional input cannot break a graph; pass `{}`
for offline tests.

Limits: 4k+1 frames, sizes a multiple of 16. The 24 Nov tests were 480x832 and 33 frames; 720p and 81 frames are NOT yet proven.
"""

from __future__ import annotations

from camera_presets import FUN_CAMERA_POSES, preset

TEXT = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VAE = "wan_2.1_vae.safetensors"
CAMERA_UNETS = (
    "wan2.2_fun_camera_high_noise_14B_fp8_scaled.safetensors",
    "wan2.2_fun_camera_low_noise_14B_fp8_scaled.safetensors",
)
VACE_UNETS = ("wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors", "wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors")
I2V_LORAS = (
    "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
    "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
)
T2V_LORAS = (
    "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
    "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
)
NEGATIVE = (
    "cartoon, painting, stylized, oversaturated, neon, glowing beam, morphing, warped, deformed, extra wings, extra limbs, text, "
    "blue tint, flicker"
)
STEPS, SPLIT, SHIFT, SAMPLER = 4, 2, 3.0, "uni_pc_bh2"


def check_size(width: int, height: int, frames: int) -> None:
    if width % 16 or height % 16:
        raise ValueError(f"width and height must be multiples of 16, got {width}x{height}")
    if frames < 5 or (frames - 1) % 4:
        raise ValueError(f"frames must be 4k+1 (33, 49, 81), got {frames}")


def node(cls: str, info: dict, **kw) -> dict:
    """A node whose required inputs not given in kw are filled from /object_info defaults (first option for combos)."""
    spec = (info.get(cls) or {}).get("input", {})
    inputs: dict = {}
    for name, v in (spec.get("required") or {}).items():
        if name in kw:
            continue
        typ, opts = v[0], (v[1] if len(v) > 1 else {})
        if "default" in opts:
            inputs[name] = opts["default"]
        elif isinstance(typ, list) and typ:
            inputs[name] = typ[0]
        elif typ == "COMBO" and opts.get("options"):
            inputs[name] = opts["options"][0]
    inputs.update(kw)
    return {"class_type": cls, "inputs": inputs}


def _front(unets, loras, prompt: str, negative: str, image_name: str | None) -> dict:
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unets[0], "weight_dtype": "default"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": unets[1], "weight_dtype": "default"}},
        "3": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["1", 0], "lora_name": loras[0], "strength_model": 1.0}},
        "4": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["2", 0], "lora_name": loras[1], "strength_model": 1.0}},
        "5": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["3", 0], "shift": SHIFT}},
        "6": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0], "shift": SHIFT}},
        "7": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT, "type": "wan", "device": "default"}},
        "8": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["7", 0]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["7", 0]}},
        "10": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
    }
    if image_name:
        g["11"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    return g


def _samplers(g: dict, cond: tuple, seed: int) -> list:
    positive, negative, latent = cond
    common = {"noise_seed": seed, "steps": STEPS, "cfg": 1.0, "sampler_name": SAMPLER, "scheduler": "simple",
              "positive": positive, "negative": negative}  # fmt: skip
    g["13"] = {"class_type": "KSamplerAdvanced", "inputs": {**common, "model": ["5", 0], "add_noise": "enable", "latent_image": latent,
                                                          "start_at_step": 0, "end_at_step": SPLIT, "return_with_leftover_noise": "enable"}}  # fmt: skip
    g["14"] = {"class_type": "KSamplerAdvanced", "inputs": {**common, "model": ["6", 0], "add_noise": "disable", "latent_image": ["13", 0],
                                                          "start_at_step": SPLIT, "end_at_step": 10000, "return_with_leftover_noise": "disable"}}  # fmt: skip
    return ["14", 0]


def _save(g: dict, images: list, prefix: str, fps: float = 16.0) -> None:
    g["16v"] = {"class_type": "CreateVideo", "inputs": {"images": images, "fps": fps}}
    g["16"] = {
        "class_type": "SaveVideo",
        "inputs": {"video": ["16v", 0], "filename_prefix": f"video/{prefix}", "format": "mp4", "codec": "h264"},
    }


def fun_camera_graph(info: dict, preset_name: str, image_name: str, prompt: str, prefix: str, width: int = 480, height: int = 832,
                     frames: int = 33, seed: int = 7, negative: str = NEGATIVE) -> dict:  # fmt: skip
    """One beat from a start image with a real camera move (Wan 2.2 Fun Camera)."""
    p = preset(preset_name)
    if p.engine != "fun_camera" or p.pose not in FUN_CAMERA_POSES:
        raise ValueError(f"preset {preset_name!r} is not a Fun Camera preset")
    check_size(width, height, frames)
    g = _front(CAMERA_UNETS, I2V_LORAS, prompt, negative, image_name)
    g["20"] = node("WanCameraEmbedding", info, camera_pose=p.pose, width=width, height=height, length=frames, speed=p.speed)
    g["12"] = node("WanCameraImageToVideo", info, positive=["8", 0], negative=["9", 0], vae=["10", 0], width=width, height=height,
                   length=frames, batch_size=1, start_image=["11", 0], camera_conditions=["20", 0])  # fmt: skip
    out = _samplers(g, (["12", 0], ["12", 1], ["12", 2]), seed)
    g["15"] = {"class_type": "VAEDecode", "inputs": {"samples": out, "vae": ["10", 0]}}
    _save(g, ["15", 0], prefix)
    return g


def vace_graph(info: dict, control_name: str, image_name: str, prompt: str, prefix: str, width: int = 480, height: int = 832,
               frames: int = 33, seed: int = 7, strength: float = 1.0, negative: str = NEGATIVE) -> dict:  # fmt: skip
    """One beat that follows a control video (an edge camera path) and keeps the keyframe's look (Wan 2.2 Fun VACE)."""
    check_size(width, height, frames)
    g = _front(VACE_UNETS, T2V_LORAS, prompt, negative, image_name)
    g["20"] = {"class_type": "LoadVideo", "inputs": {"file": control_name}}
    g["21"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["20", 0]}}
    g["12"] = node("WanVaceToVideo", info, positive=["8", 0], negative=["9", 0], vae=["10", 0], width=width, height=height, length=frames,
                   batch_size=1, strength=strength, control_video=["21", 0], reference_image=["11", 0])  # fmt: skip
    out = _samplers(g, (["12", 0], ["12", 1], ["12", 2]), seed)
    g["22"] = node("TrimVideoLatent", info, samples=out, trim_amount=["12", 3])
    g["15"] = {"class_type": "VAEDecode", "inputs": {"samples": ["22", 0], "vae": ["10", 0]}}
    _save(g, ["15", 0], prefix)
    return g


def vace_repair_graph(info: dict, control_name: str, mask_name: str, reference_name: str, prompt: str, prefix: str, width: int = 480,
                      height: int = 832, frames: int = 33, seed: int = 7, negative: str = NEGATIVE) -> dict:  # fmt: skip
    """Repair a region of a finished clip. `control_name` is the clip with the bad region painted gray, `mask_name` a video that is white
    where VACE must regenerate (both made locally by repair.py), `reference_name` a good frame of the same shot. Outside the mask the
    original frames are kept; the caller composites the result back (repair.py)."""
    check_size(width, height, frames)
    g = _front(VACE_UNETS, T2V_LORAS, prompt, negative, reference_name)
    g["20"] = {"class_type": "LoadVideo", "inputs": {"file": control_name}}
    g["21"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["20", 0]}}
    g["23"] = {"class_type": "LoadVideo", "inputs": {"file": mask_name}}
    g["24"] = {"class_type": "GetVideoComponents", "inputs": {"video": ["23", 0]}}
    g["25"] = node("ImageToMask", info, image=["24", 0], channel="red")
    g["12"] = node("WanVaceToVideo", info, positive=["8", 0], negative=["9", 0], vae=["10", 0], width=width, height=height, length=frames,
                   batch_size=1, strength=1.0, control_video=["21", 0], control_masks=["25", 0], reference_image=["11", 0])  # fmt: skip
    out = _samplers(g, (["12", 0], ["12", 1], ["12", 2]), seed)
    g["22"] = node("TrimVideoLatent", info, samples=out, trim_amount=["12", 3])
    g["15"] = {"class_type": "VAEDecode", "inputs": {"samples": ["22", 0], "vae": ["10", 0]}}
    _save(g, ["15", 0], prefix)
    return g
