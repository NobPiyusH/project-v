"""Every threshold of the gate in one place. Values are starting points; tune them against real clips (docs/quality-gate.md)."""

from __future__ import annotations

THRESHOLDS: dict[str, float] = {
    # --- Tier 1 (ffmpeg, whole video) ---
    "target_lufs": -14.0,  # streaming loudness target for a finished video
    "lufs_warn_delta": 2.0,  # |measured - target| above this warns
    "lufs_fail_delta": 6.0,  # ... and above this fails (too quiet or crushed)
    "true_peak_fail_db": 0.0,  # true peak above 0 dBTP clips
    "black_min_s": 0.25,  # shortest black run reported
    "black_edge_ok_s": 1.0,  # black at the very start/end up to this long is a fade, not a defect
    "black_fail_s": 1.0,  # black run in the middle at least this long fails (shorter warns)
    "freeze_min_s": 0.5,  # shortest frozen run reported
    "freeze_fail_s": 2.0,  # a frozen run at least this long fails (shorter warns)
    "cap_min_dur_s": 0.7,  # a caption on screen less than this is unreadable (warn)
    "cap_max_dur_s": 7.0,  # longer than this is a stuck caption (warn)
    "cap_cps_warn": 20.0,  # reading speed, characters per second
    "cap_cps_fail": 25.0,
    # --- Tier 2 (ffmpeg per-frame statistics, per shot) ---
    # motion = mean luma difference between consecutive frames (0..255 scale). A panned still scores low-to-moderate and uniform:
    # this catches near-static shots; it cannot prove the motion is real (that is the Tier 3 judge's job).
    "motion_fail_below": 0.25,
    "motion_warn_below": 0.6,
    # sharpness = mean positive Laplacian response of the frame scaled to 640 px wide (0..255 scale); low = soft/blurred.
    "sharp_fail_below": 0.8,
    "sharp_warn_below": 1.6,
    # flicker = mean |second difference| of per-frame mean luma (0..255 scale); smooth camera/lighting moves score near 0.
    "flicker_warn_above": 1.5,
    "flicker_fail_above": 4.0,
    "analysis_width": 640,  # frames are scaled to this width before sharpness is measured
    # --- Tier 3 (VLM judge) ---
    "frames_per_shot": 6,
    "accept_score": 7.0,  # weighted score 0..10 at or above this (and no major defect) accepts
    "reroll_below": 4.0,  # weighted score below this is not worth a patch: reroll the whole shot
    "max_repairs_per_shot": 2,
}

# Weighted rubric for the judge (weights sum to 1). Each criterion is scored 0 (terrible) to 10 (flawless).
RUBRIC: dict[str, tuple[float, str]] = {
    "subject_consistency": (0.35, "The same characters/objects keep the same shape, colour, size and identity across the frames."),
    "artifacts": (0.30, "No melted or extra limbs, warped faces, flicker blobs, texture swimming, text garbage or sudden colour patches."),
    "colour_drift": (0.15, "Colour grade and lighting stay stable across the frames unless the story changes them."),
    "composition": (0.20, "Framing is deliberate and the frames show real change between them, not a still image sliding."),
}
