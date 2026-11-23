# Milky Seas: director plan v2

**Status: approved by the owner, 23 Nov 2026.** Gates before publishing: firm Blender render cost approved by him, paid ElevenLabs plan, NASA/NOAA image licences checked, quality gate passed.

Short, vertical 9:16, 45 s, channel Strange If True. Machine-readable version: `milky-seas-plan.json` (the first example of the future Director plan format).

## The idea in one line
A ghost story told by sailors turns out to be real, seen from space, and still unexplained.

## Story arc and retention beats
| Beat | Time | Job |
|---|---|---|
| Hook | 0-3.5 s | Show the impossible picture first: a black sea turning white. Text asks the question. |
| Mystery | 3.5-15 s | Make it eerie: silence, scale, "like snow". |
| Turn | 15-17 s | One beat of black, then "It's real." |
| Proof | 17-24 s | Real satellite image. The only real image in the film, so it lands hard. |
| Science | 24-36 s | Glowing bacteria idea, then the honest twist: still unproven. |
| Payoff + loop | 36-45 s | Pull back over the endless glow; the last frame matches the first so the Short loops. |
A new picture or text change arrives every 3 to 5 s.

## Style guide
- Palette: near-black navy sky and sea, one milky cyan-white glow, star white. No other colours.
- Grade: soft blacks (never crushed), slight bloom only on the glow, 2% film grain.
- Camera: slow and human: drifts, gentle push-ins, a little sway at sea. No snap zooms, no effects (owner lesson: "human camera, real not effects").
- Lenses: 24 mm for scale, 85 mm for the sailor, 100 mm macro for particles.
- Text: white, small capitals, the channel font; kept out of the Shorts interface zones (top 10%, bottom 20%, right 15%).
- Glow rule: show a steady, even glow over a huge area. Do NOT show sparkles where water is splashed (that is a different kind of glowing sea).
- Labels: every Blender or AI shot that could pass for real footage gets "visualisation" or "illustration" on screen or in the description.

## Voiceover (about 105 words; low, calm, slightly breathy)
1. Sailors have told of nights when the sea turned white.
2. Not waves. Not moonlight. A silent glow, from the ship to the horizon.
3. Like sailing across snow.
4. It sounds like a sailor's ghost story.
5. It's real.
6. Satellites have photographed it from space: about a dozen glowing seas in nine years, over the Indian Ocean and the western Pacific.
7. The leading idea: billions of glowing bacteria living on algae, all shining at once.
8. But it's still an idea. No one knows for sure what switches it on.
9. So if you ever wake to a white, silent sea, you might be floating on one of the biggest light shows on Earth.
10. Strange... if true.

## Shot list
| # | Time | Shot (lens, camera, light) | Source | Sound | Text |
|---|---|---|---|---|---|
| 1 | 0-3.5 | Black sea at night, 24 mm from the bow. A thin pale line on the horizon spreads toward camera until the whole sea glows. Slow push in. | Blender B | Low drone, wood creak | THE SEA TURNED WHITE? |
| 2 | 3.5-8 | Low over the water, 24 mm, the bow cutting a flat glowing surface; stars above; slight sway. | Blender B | Water hiss, wind | |
| 3 | 8-12 | Wide aerial at night, 35 mm: a tiny ship on an endless white sea, Milky Way overhead. Very slow descent. | Blender B (high camera) | Drone swells | LIKE SNOW |
| 4 | 12-15 | Sailor at the rail, 85 mm, face lit pale blue-white from below, looks out, breath visible. | Wan (from an AI keyframe), labelled illustration | One breath, a low note | |
| 5 | 15-17 | Black. One beat of silence, then a hard cut. | none | Silence, then a low hit | IT'S REAL |
| 6 | 17-21 | Real satellite night image of a milky sea, slow push in on the glowing patch. | NASA Earth Observatory / NOAA (licence to confirm) | Low rising tone | SEEN FROM SPACE |
| 7 | 21-24 | Night-side Earth from low orbit, 50 mm, city lights, a faint pale patch on the Indian Ocean; slow orbit. | Blender A, labelled visualisation | Tone holds | ABOUT 12 FOUND · 2012-2021 |
| 8 | 24-31 | Macro, 100 mm, shallow focus: tiny glowing points drifting in dark water around a strand of algae. | Blender C, labelled visualisation | Soft crackle, warm pad enters | GLOWING BACTERIA? |
| 9 | 31-36 | Same macro; the glow slowly dims; text holds. | Blender C | Pad thins | STILL UNPROVEN |
| 10 | 36-42 | Back at sea level: slow pull back from the bow over the glowing sea to the horizon. | Blender B | Music resolves | |
| 11 | 42-45 | Holds on the opening frame (loop), then end card. | Blender B + card | Single note | STRANGE IF TRUE · Which story next? |

## Music and sound
- Music (Lyria): one cue. 0-15 s tension (sustained low strings, no beat); hit at 17 s; warm pad from 24 s; resolve at 40 s. Music sits about 10 dB under the voice.
- Sound effects: wood creak, water hiss, wind, one breath, crackle. Current Freesound step first, Earshot as fallback.

## Production and cost (estimates; Blender numbers come from the benchmark running now)
| Part | Tool | Estimate |
|---|---|---|
| Blender A, B, C (about 30 s of frames, 4K) | Cloud L40S, chunked rendering | To be set from the benchmark; ask before rendering |
| Wan shot 4 (720p, upscale later if needed) | RunPod | About $0.06 per try, 2 tries |
| Sailor keyframe | FLUX | A few cents |
| Real satellite image | NASA/NOAA | Free if public domain (confirm) |
| Voice | ElevenLabs paid plan | Plan needed before publishing |
| Music | Lyria | About Rs 10-20 |
| Quality gate | OpenAI | About $0.10 |

## Facts and sources (checked 23 Nov 2026)
- First satellite detection reported in 2005 (Miller et al., PNAS, DMSP data).
- 2021 Scientific Reports paper "Honing in on bioluminescent milky seas from space": VIIRS Day/Night Band, 12 events from 2012 to 2021, Indian Ocean and far western Pacific.
- Bacteria (Vibrio harveyi) on algae: from water samples of a 1995 ship sighting; a hypothesis, not proven.
- Do not state: any area figure, the year of the Java event (sources disagree), what triggers it.
- To verify before using in the voiceover: that the glow is steady rather than triggered by movement (used only as a visual rule for now).
