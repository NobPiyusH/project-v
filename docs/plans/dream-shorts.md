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
