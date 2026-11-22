"""2K master: size selection and the scale/sharpen chain for mixed sources (no ffmpeg run)."""

from workers.activities import assemble as A


def test_scale_chain_sharpens_only_enlarged_clips():
    drawn = A.scale_chain(1440, 2560, 1440)  # native 2560x1440 diagram
    ai = A.scale_chain(720, 2560, 1440)      # 720p AI clip enlarged 2x
    assert A.SHARPEN not in drawn and A.SHARPEN in ai
    assert ai[0] == "scale=2560:1440:force_original_aspect_ratio=increase:flags=lanczos" and ai[1] == "crop=2560:1440"


def test_default_master_is_unchanged_and_1440_is_opt_in():
    assert A.SIZES["16:9"] == (1920, 1080)
    assert A.SIZES_16_9[1440] == (2560, 1440) and A.SIZES_16_9[2160] == (3840, 2160)
