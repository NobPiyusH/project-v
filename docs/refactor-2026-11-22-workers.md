# Workers refactor, 22 Nov 2026

Scope: `workers/` only. No behaviour change. Tests: `uv run pytest workers/tests`: **199 passed, 1 skipped** (was 196 + 1 skipped before; 3 new tests).

## Removed (with proof it was unused)
| What | Proof |
|---|---|
| `dream/hook.py::apply_hook` | `grep -rn apply_hook workers control-plane/src`: only `tests/test_dream_hook.py`. `dream/finish.py` burns the hook with `hook_ass` + `filter_for` itself. The visual test is kept, using a 10-line local helper that does what `dream/finish.py` does. |
| `dream/scenes.py::beats_for` | Same grep: only `tests/test_svi_scenes.py`. Its `scene_frames` assertions are kept. |
| `dream/finish.py`: `climax_at` local | Assigned, never read (ruff F841). |
| Unused import in `tests/test_dream_hook.py` / `test_model_fallback.py`, an f-string with no placeholders in `pod/client.py` | ruff F401/F541. |

Checked and **kept** (not dead): every activity in `workers/main.py` maps to an `@ActivityMethod` in `control-plane/.../MediaActivities.java`. All four Modal apps (`wan`, `upscale`, `finish`, `zimage`) are looked up by name at runtime (`modal.Cls.from_name` in `activities/clips.py`, `activities/finish.py`, `activities/keyframes.py`), so a grep for imports makes them look unused when they aren't. The features you switched off on purpose (first-last bridge, ACE-Step, Z-Image, Nano Banana) are untouched. `vulture` at 60% confidence reported nothing else besides Temporal/Modal/HTTP-handler entry points and the JSON-schema field names.

## Changed
- **New `workers/groq_whisper.py`**: the Groq Whisper multipart POST was written out twice, in `activities/voice.py::_whisper` (narration check) and `activities/assemble.py::_whisper_words` (caption word timings). Both now call `groq_whisper.transcribe(wav, extra_fields)`. The request is byte-identical: the field order, the `line.wav` filename, the 60 s timeout and the headers are all the same, and a test pins the body against the old hand-built one. Each caller still catches failures and keeps its own warning and fallback (Gemini check / even word spacing).

## Left alone (on purpose)
- **`workers/pod/server.py`**: ruff-clean, and it is shipped to pods and read by the backend on every rental while a paid SVI test is running. Refactoring its internals buys little and adds risk to the live contract. Recommended as its own change once the test is over.
- **The four `_probe` functions** (finish, footage, inspect, local/rife): each asks ffprobe for different fields in a different output format, so merging them would mean rewriting their parsers. The gain is small and the risk is real.
- **`plan_frames`**, written out in both `local/rife.py` and `modal_apps/finish.py`: Modal apps are deployed as self-contained files.
- **The two ASS/SRT `_ts` formatters**: they round differently, and merging them would change caption timings.
- Modal app warnings (an unused `timestep` variable, an unused import): editing them means redeploying.

## Risks
- Low. The only runtime path that changed is the Groq Whisper call, and a test shows its request bytes are unchanged. The running local worker keeps the old code until it is restarted.
