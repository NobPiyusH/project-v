"""One-off generator for docs/shorts/ant-hears-at-night-plan.json (25 Nov 2026): the owner's approved 9-line script, nine scenes of two
5 s beats (18 beats, 90 s), only tested camera presets, one ground-level night-meadow bank location. Run: python -m tools.film4k.make_ant_plan"""

import json
from pathlib import Path

LOCATION = (
    "A real-looking meadow floor at night seen from a few centimetres above the ground: dark soil, fine grass blades and tiny pebbles, "
    "beads of dew catching cold blue moonlight, soft out-of-focus background, shallow depth of field, photographic, no people, no animals, no text."
)

# (scene title, claim, beat 1 prompt, beat 2 prompt, line)
SCENES = [
    ("Dew and silence",
     "An ant has no ears like ours.",
     "Still ground-level view of dew beads on grass blades and dark soil in faint moonlight, very calm, nothing moving but a slow push toward the dew",
     "Same scene continuing: the dew beads tremble very slightly, the grass barely moves",
     "An ant has no ears. So what does it hear?"),
    ("Air pulses",
     "Ants sense air movement through organs in their antennae (Johnston's organ).",
     "Ground-level view, a faint pulse of moving air bends the fine hairs on a leaf edge and shivers the dew drops, soft moonlight, no creatures",
     "Same view: a second gentle pulse of air passes across the frame, dew drops shiver and settle",
     "Its antennae feel pulses in the air. Even a distant moth can bend them, like a tiny, passing pressure."),
    ("Footsteps in the soil",
     "Ants' leg organs (subgenual organs) are the main receptors for vibrations through the ground.",
     "Dark meadow soil at ground level, a slow ripple ring spreads through the fine soil surface and bends tiny grains, as from a distant footfall",
     "Same soil: a second fainter ripple ring crosses the frame, grains tremble and dew shivers on the nearest blades",
     "Below it, the ground answers faster. A far footstep sends ripples through soil, felt through the legs."),
    ("Raindrop",
     "A raindrop impact sends vibrations through the soil.",
     "Ground-level meadow at night, a single raindrop falls into the frame and lands on the dark soil, droplets leaping up",
     "Same spot: a ring-shaped shockwave spreads outward through the soil and grass, dew beads jump and settle",
     "Then one raindrop hits. The dirt shivers, and a shockwave spreads outward through the meadow floor."),
    ("The colony",
     "Some ants drum or tap to alert their nest (alarm drumming in carpenter ants).",
     "A small round tunnel opening in dark soil edged with crumbs of earth, cool moonlight, perfectly still",
     "Same tunnel mouth: faint tremors make the crumbs at its rim shiver in a slow rhythm",
     "At the tunnel mouth, some ants drum to warn the nest. In the dark, touch becomes a message."),
    ("The road",
     "Distant traffic can travel through the ground as low-frequency vibration.",
     "Wide calm night meadow floor, long slow pulses move through the soil, dew beads trembling in time, a faint glow of a far road on the horizon",
     "Same view: the slow deep pulses continue, the tips of the grass sway very slightly",
     "Far away, a road becomes a slow hum in the ground. Not sound exactly — more a pressure you can feel."),
    ("The giant step",
     "A heavy footstep makes a large ground vibration.",
     "Ground-level meadow, the whole soil surface begins to shake heavily as one enormous footstep lands out of sight, dew flying off leaves",
     "Same shake fading: the soil settles, dew falls back onto the leaves, fine dust drifts in the moonlight",
     "Then comes the giant step. For a moment, the whole meadow seems to lurch like an earthquake."),
    ("Quiet",
     "Wind moves grass and air; the night between events is calm.",
     "Calm night meadow at ground level, a gentle wind moving the grass, the dew steady, soft moonlight",
     "Same calm: the wind slowly eases and stillness returns to the meadow floor",
     "After that, only wind in the grass. A long calm, where nothing touches, and everything listens."),
    ("Dawn",
     "The night is full of vibration we cannot feel.",
     "First pale light on the meadow floor, dew glittering, the sky turning faint blue and pink above the horizon",
     "Same view as the light grows, the dew sparkling, the meadow calm and still",
     "Dawn finds the dew again. The night is loud. We just can't feel it."),
]

# tested-good presets only. ONE move per scene (both 5 s beats use it): changing move every beat looked like "left, straight, left" (owner, 25 Nov 2026);
# neighbouring scenes still differ so the film does not feel static, and the slow push is the base move.
SCENE_CAMERAS = ["slow_push", "drift_right", "slow_push", "zoom_in", "slow_push", "pan_right", "slow_push", "drift_right", "slow_push"]
CAMERAS = [c for c in SCENE_CAMERAS for _ in range(2)]
CONT = "same meadow floor, same cold blue moonlight, same camera height and look as the previous beat"


def build() -> dict:
    scenes, script, claims_flat = [], [], []
    for i, (title, claim, b1, b2, line) in enumerate(SCENES):
        t0 = i * 10.0
        scenes.append({
            "lengthSec": 10,
            "title": title,
            "beats": [
                {"prompt": b1, "locationIds": ["night-meadow"], "startSec": 0.0, "durationSec": 5.0, "claims": [claim]},
                {"prompt": b2, "locationIds": ["night-meadow"], "startSec": 5.0, "durationSec": 5.0, "continuity": CONT},
            ],
        })
        script.append({"n": i + 1, "text": line, "startSec": t0 + 0.4, "beat": 2 * i + 2})
        claims_flat.append(claim)
    return {
        "plan_version": "2.0",
        "title": "What an ant hears at night",
        "channel": "Strange If True",
        "note": "Owner brief 25 Nov 2026. Script lines and facts approved from the pipeline run 539db64a (research 8 facts); redo as a plan file so the beats are 5 s each, calm, and only tested camera presets are used.",
        "video": {
            "title": "What an ant hears at night",
            "summary": "A 90-second vertical film: a night meadow seen from ground level, where sound is shown as ripples in soil, trembling dew and the shockwave of a raindrop, to show what an ant senses.",
            "format": "long_form",
            "durationSec": 90,
            "aspectRatio": "9:16",
            "outputResolution": "1080p",
            "stylePreset": "ambient_visual",
            "tone": "Calm, curious, slightly awe-struck; photoreal, never cute or cartoonish.",
            "language": "en",
            "needsResearch": False,
            "narration": "full",
            "voice": "Calm, warm, unhurried narrator",
            "music": "none",
            "soundDesign": True,
            "visualTypes": ["ai_video"],
            "shortsCount": 0,
            "mustInclude": ["no ant, people or animals visible", "ground-level night meadow", "sound shown as ripples, dew tremors and shockwaves", "no on-screen text"],
            "rationale": "Nine scenes of two 5 s beats; one consistent look; slow camera moves only.",
        },
        "generation": {
            "width": 720, "height": 1280, "frames": 81, "fps": 16,
            "engine": "wan2.2 fun_camera (4-step lightx2v, fp8)",
            "cameraPresets": CAMERAS,
            "style": "photoreal night macro, cool blue moonlight, natural motion, no text, no creatures",
        },
        "bank": {"locations": [{"id": "night-meadow", "description": LOCATION}]},
        "script": script,
        "scenes": scenes,
    }


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[2] / "docs" / "shorts" / "ant-hears-at-night-plan.json"
    out.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", out)
