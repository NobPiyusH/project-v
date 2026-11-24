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
GROUPS = {"fun_camera": FUN_CAMERA, "vace": VACE, "seedvr2": SEEDVR2}
SEEDVR2_NODE_SHA = "4490bd1f482e026674543386bb2a4d176da245b9"  # numz/ComfyUI-SeedVR2_VideoUpscaler, pinned on 24 Nov 2026


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
