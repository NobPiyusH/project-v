# Dream Shorts: plan (draft for the owner's approval, 18 Nov 2026)

Branch `dream-shorts`. Nothing below is built until the owner approves it. Paid tests are asked for separately.

## What we are making
Short, surreal, music-led vertical videos for the **Dreamscapes** channel, like the owner's examples:
- **Dreamcore** (main target): 4-5 shots of one pastel dream world, slow walking camera, soft/hazy/grainy film look,
  ~15 s, ambient music, no narration.
- **Impossible place** (later preset): one surreal photo-real scene, one slow camera move, 8-12 s.

Owner targets (18 Nov 2026): **under ₹20 per Short**, no Google Flow, laptop first where possible, quality first.
Every idea is our own (the examples are other creators' work: we copy the format, never their scenes).

## Impact on the existing pipeline: none
- The full video pipeline (story, voice, shots, clips, finish, master) is **not edited**. Its tables, workflows,
  stages and screens stay as they are.
- Dream Shorts get their **own** tables, workflow, API and screen, and **reuse** existing engines by calling them,
  not by changing them: object storage, provenance + licence, cost ledger, budget caps, router, the Modal Wan app,
  the Modal Z-Image app, laptop RIFE smoothing.
- Small additive touch points only: one menu link, one workflow registration (Java), one activity registration
  (Python worker), one new capability on the existing `modal_wan` provider in `config/providers.yaml`, one
  migration with new tables.

## The screen: "Dream Shorts" (new tab next to Studio and Providers)
1. **Idea**: type one ("a pink house under a cloud at the end of a tiled pier") or press *Suggest* for 5 fresh ideas
   (never repeats one already made). Pick the look (Dreamcore / Impossible place), length, clip quality (480p / 720p).
2. **Pictures**: the studio writes 4-5 shot prompts in one world (same palette, same light) and makes one picture per
   shot. Approve, redo one, or edit its prompt.
3. **Clips**: each picture is brought to life with Wan (slow walking camera). Shows the provider, estimate (time and
   ₹) and the routing reason before starting. Redo any clip with a new seed.
4. **Finish**: smooth to 24 fps, dream look (grain, haze, soft glow, vignette, faded colour), music from the owner's
   free music folder, loudness to Shorts level, cut to length. Preview and download the MP4.
- Every step runs in **Auto, Review or Manual** (rule 2a); a failed step waits for the owner with Retry / Continue
  (rule 2b); Stop cancels GPU work at once (rule 8).

## Backend
- **Control plane** (`com.projectv.dream`): `DreamShortService` (plans shots, estimates, saves results),
  `DreamShortWorkflow` (Temporal, deterministic: ideas → pictures → clips → finish, owner gates per mode),
  `DreamController` (`/api/dream/...`). Tables `dream_short`, `dream_shot`; every picture and clip is an `asset`
  with provenance; every paid job a `cost_entry`; every provider choice a `routing_decision` (rule 6).
- **Shot writer**: one LLM call (existing Llm/fallback chain) turns the idea + look into 4-5 shot prompts sharing one
  "world bible" (palette, light, materials, lens), so the shots look like one place.
- **Router**: new capability `video.i2v.dream`. Candidates by phase: `modal_wan` (now), `local_wan` (laptop, after
  the SSD), `vast_5090` (after M3c). Owner rule: laptop first when its estimated time fits the deadline; cloud only
  within the ₹20 target and the budget caps.
- **Python worker**: activity `dream_finish` (FFmpeg: grain, haze, glow, vignette, colour fade, music mix, loudness)
  in `workers/dream/`; pictures and clips call the existing Modal apps.

## Cost and time per Short (5 clips × 5 s; estimates, measured in the first test)
| Phase | Clips on | ₹ per Short | Time |
|---|---|---|---|
| D1 | Modal Wan, fast 480p (H100) | ≈ ₹17-28 on paper, **₹0 actual** inside the free $30/month credit | ~5 min |
| D2 | Laptop Wan 2.2 14B Q4 GGUF | **₹0** | ~3-5 h (overnight queue) |
| D3 | Vast.ai RTX 5090, batches of 5 Shorts | ≈ ₹9-15 at 720p (live price, ~$0.45-0.78/h) | ~13 min each + 10 min per batch |
Pictures: Z-Image on Modal ≈ ₹1-3. Finishing and music on the laptop: ₹0.

## Phases (each ends with a demo and the owner's OK)
- **D1 (≈1-1.5 weeks)**: tables, workflow, API, screen, Modal Wan + Z-Image, dream finish, one test Short. No new
  accounts; spend inside the Modal free credit.
- **D2 (after the SSD)**: laptop Wan 14B Q4 (ComfyUI or diffusers + GGUF), overnight queue, side-by-side with Modal.
- **D3 (only if the owner approves Vast.ai)**: M3c pod manager first (idle shutdown, orphan sweep, verified hosts),
  then 5090 batches. Owner creates the account and adds credit.
- **Later, separate plan**: Blender science track (Tiny Worlds, scale, diagrams), laptop-first.

## Tests
Workflow replay/determinism, mode gates (Auto/Review/Manual), failure → waits, Stop cancels, router choice logged,
cost entries and provenance written, finish filter graph (FFmpeg) on a sample clip, API contract.

## Risks
- **Quality of Wan 480p**: the dreamcore look forgives softness; the D1 test Short decides. 720p is one setting away.
- **YouTube "inauthentic content" rules**: new idea every time, *Suggest* never repeats, varied worlds and music.
- **Music licence**: only tracks the owner adds to the music folder (licence noted per track) or no music.

## Decisions for the owner
1. Approve D1?
2. Default clip quality: 480p (cheapest, fits the look) or 720p?
3. Music: a folder of free tracks you choose, or Lyria (≈ ₹10, over the ₹20 target with 720p)?
4. Screen name: "Dream Shorts" OK?

## Decisions made for the owner's approval (19 Nov 2026)

The owner asked me to handle the technical decisions; he approves. Built on `dream-shorts` (commits f4f6996, 827447d, 964ccea).

| # | Decision | Why | Cost |
|---|---|---|---|
| 1 | Every Short must feel warm, nostalgic or feel-good, and look real (never a render or an obvious AI image); written into the writer and every picture prompt | Owner's rule from his 7 examples | ₹0 |
| 2 | Looks: Lonely object (default), Drive-by (new), Walk-through, plus the old Dreamcore and Impossible place | Covers all the examples; variety stays inside rule 1 | ₹0 |
| 3 | Lonely object and Drive-by join shots with hard cuts; Walk-through uses 0.35 s dissolves | Matches the examples | ₹0 |
| 4 | Pictures stay on Z-Image | Owner's choice after the side-by-side | ≈ ₹0.2 a picture |
| 5 | Music: the owner's track by default; 6 candidate pieces, each starting on a strong beat; Gemini listens with the pictures and keeps the best fit; Lyria stays a per-Short option | Owner: "they are syncing with the music" | ≈ ₹0.6 a Short |
| 6 | Cuts land on the music's beats (a clip may lose up to 1 s, never grows) | Same | ₹0 |
| 7 | A whisper of moving film grain on the realistic looks, no haze or vignette | Breaks the too-smooth AI surface | ₹0 |
| 8 | NOT built yet: one long 10 s take (two clips joined from the last frame) | Needs a second GPU round per Short (+ ≈ ₹13.5 start-up) | later, with batching |
| 9 | Pending the owner: 720p clips as the default for the new looks | "Best graphics", but ≈ 2× the clip cost of 480p | ≈ ₹25–33 a Short |

## Lessons learned (19 Nov 2026)

What went wrong in the first Dream Shorts tests, and the rule each one became:

1. **The 720p clip estimate was half the real cost** (said ₹25, cost ₹47). Never quote a price from scaling a guess:
   use a measured number, and after each new kind of paid run, put its real cost back into `providers.yaml`.
2. **A hard cut was silently read as a 0.5 s dissolve** (`value or 0.5` treats 0 as missing). Settings where 0 means
   something must be read with an explicit "is it set?" check, and get a test for the 0 case.
3. **The first music bed matched nothing** (a random spot in an hour of music). Anything chosen for taste (music,
   pictures) is judged by ear or eye, not only by a number like loudness.
4. **The camera looked like AI** (fast, perfect glide). Compare every new look against the owner's examples on
   measured motion and colour before calling it fine, and write camera prompts as a person filming, not a camera rig.
5. **Haze and grain made it look worse, not dreamier.** The owner wants it real; effects that announce themselves are out.
6. **Before saying "something approved this", read the workflow history**: signals record when and from where.

## Hook title, chained shots and the idea pack (20 Nov 2026, all OFF by default)

Three per-Short options for the "impossible architecture" style (a continuous drift through one warping city, a big
two-colour title in the first seconds). Each is a checkbox on the Start form and a field on `POST /api/dream`; a Short
that does not ask for them is unchanged (migration V34 adds the columns with default off).

| Option | Form / API | What it does |
|---|---|---|
| **Hook title** | "Hook title" + text + "Big word" colour; `hookTitle`, `hookText`, `hookColor` | The writer also returns a 3-6 word curiosity hook (`THE STREET *FOLDED* LIKE PAPER`; the starred word is the big one). The Finish burns it in for ~1.8 s (`workers/dream/hook.py`, libass): up to 3 lines, white outlined text, one larger coloured word (red by default; yellow, orange, pink, cyan or `RRGGBB`), centred 30 % down with 10 % side margins, fade in/out. Blank text = the writer's (or the idea's) hook. Edit it on the Short's page (`POST /api/dream/{id}/hook`), then "Redo from Finish" (free); an edited hook is locked, so a rewrite never replaces it. |
| **Chained shots** | "Chain shots"; `chainShots` | Clip N+1 starts from the last frame of clip N (`last_frame_png` in `workers/activities/clips.py`), so the camera flows through one scene. Clips run one at a time on both routes (Modal: one `generate_one` call per clip on a warm container; broker: in order on the pod). The Write step describes ONE continuous camera move in ONE world. Per-clip cost entries are unchanged; a failed clip leaves the ones after it waiting (they fail fast, no GPU time) until the owner retries or redoes it (rule 2b); a redone shot continues from the clip before it. Redoing a shot does not redo the ones after it: redo them too if the join matters. |
| **Idea pack** | "Idea pack" picker; `ideaPack` + `ideaId` | `control-plane/src/main/resources/dream/idea-packs.json` holds 24 original concept seeds (twisting towers, looping bridges, folding streets, stairs in the sky, ...), each with a hook and a camera note. Picking one fills the idea, sets the `architecture` look, skips the Scout (nothing to research) and hands the seed to the writer. `GET /api/dream/idea-packs` lists them. Add packs by editing the JSON; no code change. |

The `architecture` look (clean photo-real city, 2.5 s shots, hard cuts, its own camera rule: slow continuous glide) is
selectable but not in `LOOK_ORDER`, so the Autopilot's daily rotation does not pick it.
Chain length (`chainGroup`, the "Chain" menu): 0 = one chain through the whole Short; 2 = pairs (shots 1+2 chained, a cut,
then 3+4, ...); 3 = threes. Each chain's first shot has its own picture; a failed shot only breaks its own chain.
**Locked scene** (`chainLocked`, only with one chain through the whole Short): like a single camera move through one 3D set.
The writer plans ONE camera path first and cuts it into the shots (`cameraPath`); every clip's prompt restates that path and
the one world; all clips share one seed; and the worker lightly denoises and sharpens each last frame before the next clip
starts from it (`CLEAN_FILTER` in `clips.py`), so quality sags less with every link. Not built: re-anchoring from a fresh
picture when a quality check says a frame has degraded; judge drift on the first real trial (pairs are the safe fallback).
Chaining makes pictures only for the first shot of each chain; the other shots start from the clip before them, so they
have no picture of their own and no silent fallback: if a last frame can't be read the clip fails and waits for the owner
(Retry / redo the shot before it). Not built: two candidate pictures for shot 1 (redo shot 1's picture meanwhile).
