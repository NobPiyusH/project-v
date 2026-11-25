"""Optional model-file groups for a pod, so a job only downloads what it needs (the boot was 80 GB for three tests: 16 min of deps
competed with the download). Sizes are the byte counts shown on Hugging Face on 24 Nov 2026. Boot time at about 100 MB/s is an ESTIMATE
from the measured 41 GB in 7 min (24 Nov 2026) and the 80 GB boot of about 14 min.
"""

from __future__ import annotations

R = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
SV = "numz/SeedVR2_comfyUI"
LX = "lightx2v/Wan2.2-Distill-Loras"
GB = 1e9


def _f(repo: str, path: str, sub: str, size: int) -> tuple[str, str, str, int]:
    return (repo, path, sub, size)


COMMON = {
    "umt5_xxl_fp8_e4m3fn_scaled.safetensors": _f(
        R, "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders", 6735906897
    ),
    "wan_2.1_vae.safetensors": _f(R, "split_files/vae/wan_2.1_vae.safetensors", "vae", 253815318),
}
FUN_CAMERA = {
    "wan2.2_fun_camera_high_noise_14B_fp8_scaled.safetensors": _f(R, "split_files/diffusion_models/wan2.2_fun_camera_high_noise_14B_fp8_scaled.safetensors", "diffusion_models", 15301408048),
    "wan2.2_fun_camera_low_noise_14B_fp8_scaled.safetensors": _f(R, "split_files/diffusion_models/wan2.2_fun_camera_low_noise_14B_fp8_scaled.safetensors", "diffusion_models", 15301408048),
    "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors": _f(R, "split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", "loras", 1226977424),
    "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors": _f(R, "split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", "loras", 1226977424),
}  # fmt: skip
VACE = {
    "wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors": _f(R, "split_files/diffusion_models/wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors", "diffusion_models", 17346056104),
    "wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors": _f(R, "split_files/diffusion_models/wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors", "diffusion_models", 17346056104),
    "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors": _f(R, "split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors", "loras", 1226977424),
    "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors": _f(R, "split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors", "loras", 1226977424),
}  # fmt: skip
SEEDVR2 = {
    "ema_vae_fp16.safetensors": _f(SV, "ema_vae_fp16.safetensors", "SEEDVR2", 501324814),
    "seedvr2_ema_3b_fp8_e4m3fn.safetensors": _f(SV, "seedvr2_ema_3b_fp8_e4m3fn.safetensors", "SEEDVR2", 3391544696),
}  # fmt: skip
ANIMATE = {  # Wan 2.2 Animate (Apache-2.0): body and face motion from a driving video onto a character image. Sizes from Hugging Face, 25 Nov 2026.
    "Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors": _f("Kijai/WanVideo_comfy_fp8_scaled", "Wan22Animate/Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors", "diffusion_models", 18401760586),
    "clip_vision_h.safetensors": _f("Comfy-Org/Wan_2.1_ComfyUI_repackaged", "split_files/clip_vision/clip_vision_h.safetensors", "clip_vision", 1264219396),
    "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors": _f("Kijai/WanVideo_comfy", "Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors", "loras", 738005744),
}  # fmt: skip
GROUPS = {"fun_camera": FUN_CAMERA, "vace": VACE, "seedvr2": SEEDVR2, "animate": ANIMATE}
NODE_SHAS = {  # custom nodes pinned on 25 Nov 2026
    "ComfyUI-KJNodes": ("kijai/ComfyUI-KJNodes", "187868f53a7d9df007dbfe85891dab41453f7933"),
    "comfyui_controlnet_aux": ("Fannovel16/comfyui_controlnet_aux", "c2655d2970d810d8a3d71deed204cdf416b66132"),
    "ComfyUI-Frame-Interpolation": ("Fannovel16/ComfyUI-Frame-Interpolation", "26545cc2dd95bc3d27f056016300673bdeee78f5"),
}
SEEDVR2_NODE_SHA = "4490bd1f482e026674543386bb2a4d176da245b9"  # numz/ComfyUI-SeedVR2_VideoUpscaler, pinned on 24 Nov 2026

PINNED_ON = "2026-11-25"  # sha256 and commit hashes below were read from the public Hugging Face API (no weight downloads) on this date
REVISIONS = {  # Hugging Face repo -> commit the files below were pinned at (hf_hub_download(revision=...))
    "Comfy-Org/Wan_2.2_ComfyUI_Repackaged": "5df3a4a3ed0628002308022198249dbbbd045d09",
    "numz/SeedVR2_comfyUI": "09ced71023636e9bc8cdf9cdecfb2625d1e691e8",
    "Kijai/WanVideo_comfy_fp8_scaled": "033a4e487f60220b3d6e469599a6aebc46e13cee",
    "Comfy-Org/Wan_2.1_ComfyUI_repackaged": "d6258323e18f14c60d37899da97ca555f3118259",
    "Kijai/WanVideo_comfy": "8260d429d19fd7a72304cad059160b95d843913f",
    "lllyasviel/Annotators": "982e7edaec38759d914a963c48c4726685de7d96",
}
SHA256 = {  # file name -> sha256 of the whole file (Hugging Face LFS oid); the pod verifies every download against it
    "umt5_xxl_fp8_e4m3fn_scaled.safetensors": "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68",
    "wan_2.1_vae.safetensors": "2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b",
    "wan2.2_fun_camera_high_noise_14B_fp8_scaled.safetensors": "89a99cb5ab29e388f2e24fddac48eab4d88ca339d236c7a8d96aabacd0f525ae",
    "wan2.2_fun_camera_low_noise_14B_fp8_scaled.safetensors": "a2b7b361e3fad907958aa6ad662a3673bad5ffdce14706cffde4baabb98f9020",
    "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors": "d176c808d6fc461999b68e321efcb7501b20b8c3797523ed0df14f7d1deff11e",
    "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors": "024f21de095bc8fad9809ded3e9e49a2e170dcf27075da8145ba7d60d8aab7f9",
    "wan2.2_fun_vace_high_noise_14B_fp8_scaled.safetensors": "23130f30207f6c8697a5bead113ddc9904cfbb5e8fa51a550df3cee4d21fb54c",
    "wan2.2_fun_vace_low_noise_14B_fp8_scaled.safetensors": "ca55a5cc543e28576edbd2079f8c1de05f71cd9e85bf96fab635f55663dbcb69",
    "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors": "698321cb86bd30c4af06c9b84e656a1048c8cb54e06d50694536fb5de37fde41",
    "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors": "ec95216e614b3c132c11bfb387b11feedf62163150ccc9068bca8a189771e75a",
    "ema_vae_fp16.safetensors": "20678548f420d98d26f11442d3528f8b8c94e57ee046ef93dbb7633da8612ca1",
    "seedvr2_ema_3b_fp8_e4m3fn.safetensors": "3bf1e43ebedd570e7e7a0b1b60d6a02e105978f505c8128a241cde99a8240cff",
    "Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors": "2936b31473a967e7a429a6646bba60e7862d0938e178b58b2a140f391dd5b8e6",
    "clip_vision_h.safetensors": "64a7ef761bfccbadbaa3da77366aac4185a6c58fa5de5f589b42a65bcc21f161",
    "lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors": "85c4a61c30e0497aa44b91d93a893b624708461a56fe5485183b28fa07e2dfb3",
    "RealESRGAN_x4plus.pth": "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1",
}  # fmt: skip
ESRGAN = ("lllyasviel/Annotators", "RealESRGAN_x4plus.pth", "upscale_models", 67040989)  # the pod boot used a floating /resolve/main/ URL before the pin
IMAGES = {  # floating-looking pod images -> their immutable digest (Docker Hub manifest-list digest, read 25 Nov 2026). Use `repo@digest` to pin.
    "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404": "sha256:0a360022e8de4375af99430f84e8b38951acc397252163a37ceac7204d01be35",
    "runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04": "sha256:cb154fcca15d1d6ce858cfa672b76505e30861ef981d28ec94bd44168767d853",
}


def files_for(groups: list[str] | tuple[str, ...]) -> dict[str, tuple[str, str, str, int]]:
    """The files to download for the named groups (plus the text encoder and VAE every Wan job needs), without duplicates."""
    unknown = [g for g in groups if g not in GROUPS]
    if unknown:
        raise KeyError(f"unknown model group(s) {unknown}; choose from {sorted(GROUPS)}")
    out = dict(COMMON)
    for g in groups:
        out.update(GROUPS[g])
    return out


def total_gb(groups: list[str] | tuple[str, ...]) -> float:
    return sum(v[3] for v in files_for(groups).values()) / GB


def boot_minutes(groups: list[str] | tuple[str, ...], mb_per_s: float = 100.0, base_min: float = 3.0) -> float:
    """ESTIMATED minutes until models are on disk: download at mb_per_s plus about `base_min` for ComfyUI and pip."""
    return base_min + total_gb(groups) * 1000 / mb_per_s / 60


def revision_for(repo: str) -> str:
    """The pinned commit of a Hugging Face repo."""
    return REVISIONS[repo]


def pins_for(files: dict) -> dict[str, dict[str, str]]:
    """name -> {"sha256", "revision"} for the files sent to a pod. A file nobody pinned is left out (the pod prints UNPINNED for it)."""
    out = {}
    for name, v in files.items():
        if name in SHA256 and v[0] in REVISIONS:
            out[name] = {"sha256": SHA256[name], "revision": REVISIONS[v[0]]}
    return out


def pinned_image(image: str) -> str:
    """`repo:tag` -> `repo@sha256:...` when the digest is known, else the image unchanged."""
    d = IMAGES.get(image)
    return f"{image.split(':')[0]}@{d}" if d else image
