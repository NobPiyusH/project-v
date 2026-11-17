# Roadmap: one studio, many formats (draft for the owner's approval, 18 Nov 2026)

## Decisions (recommended; the owner confirms or changes each)

| # | Decision | Recommendation | Why |
|---|---|---|---|
| D1 | Architecture | **One engine + format recipes + channels, in this repo** | ~80% of the work (workflow, budgets, checks, voice, edit, publishing, router) is shared; separate projects would duplicate it. Each format keeps its own rules and quality bar. |
| D2 | Formats, first wave | **F0** Cinematic documentary Short (exists) · **F1** Low-poly Short · **F2** 3D Explainer (Short + long) · **F3** How-it-works · **F4** Cosmic epic (MelodySheep-inspired) long-form. "What if" and facts Shorts reuse F2. | Covers the owner's four ideas; F1/F2/F3 share the Blender work. |
| D3 | Channels | **Start with one English science/space/nature channel**, formats as series inside it; split a format into its own channel only when it proves itself (views, retention). | One audience to grow first; splitting later is easy, merging is not. |
| D4 | Language | **English first**; other-language captions later; dubbed audio (Tamil/Hindi) after M5. | Quality and checks (Whisper, captions) are proven in English. |
| D5 | Voice | **Owner picks from the 5 samples.** If Chatterbox sounds right: it becomes the main voice (free, commercial-safe), optionally cloned from the owner's own voice; ElevenLabs paid plan only if monetising with it; Kokoro stays the safety net. | ElevenLabs Free is non-commercial. |
| D6 | Blender | **Cloud Blender on Modal now** (no SSD needed); the laptop renders free once the SSD is in; M3's router picks between them. | Unblocks F1–F3 today; fast, parallel, auto-shutdown. |
| D7 | Spending | Keep a cost gate before every GPU stage; raise the Modal cap only for the F4 pilot (owner approves the amount); auto-approve stays ₹0 unless the owner sets a small limit (e.g. ₹10 for smoothing). | Rule 2: ask before spending. |
| D8 | Quality rule | Every new format and every saving is **off by default until a side-by-side shows it holds up**; each format has a pilot the owner approves. | The owner's standing rule. |
| D9 | Big assets | Blender kits, 3D models and textures live in storage (Google Drive / Hugging Face), **not in git**; the repo holds recipes and template code only. | Keeps the repo small; licences recorded per asset. |

## Phases (each ends with a demo and the owner's approval, CLAUDE.md rule 1)

| Phase | What | Effort (part-time) | Spend |
|---|---|---|---|
| **A** | **Formats framework** (a recipe file per format: planner rules, shot-source rules, edit mode, look, voice, music) + **channel setting** + **cloud Blender app on Modal** + a 5 s render test, Eevee vs Cycles | ~1 week | ≈ ₹10–20 test |
| **B** | **F1 Low-poly**: first kit (ocean & island; CC0 assets), palette/lighting presets, pilot 60 s Short | ~1.5–2 weeks | ≈ ₹30–60 pilot |
| **C** | **M3a + M3b: router + dashboard** (docs/plans/M3-router.md) | ~2–2.5 weeks | none new |
| **D** | **F2 3D Explainer**: block library (counters, scale bars, labels, maps, camera paths) + per-shot source choice (Blender / AI / real) + pilot Short | ~2 weeks | ≈ ₹60–120 pilot |
| **E** | **F3 How-it-works**: cutaway / exploded-view toolkit, CC0/CC-BY mechanism models + pilot Short | ~2 weeks | ≈ ₹60–120 pilot |
| **F** | **F4 Cosmic epic**: music-led edit mode, chained long shots, 4K copy + a **2–3 min pilot** | ~2 weeks | ≈ ₹1,000–1,600 (GPU cap raise needed) |
| then | **M4** quality layer · **M5** publishing & learning · **M6+** series/characters (drama), style LoRA | per HANDOVER §23 | — |

Total to all four pilots: ~10–12 weeks part-time. Paused until the owner says so: SSD move (laptop Blender), drama series, Vast.ai (M3c).

## Cost per video once built (estimates; pilots measure the real numbers)

| Format | Per video |
|---|---|
| F0 Cinematic documentary Short | ≈ ₹225–265 (₹200–250 with real footage) |
| F1 Low-poly Short | ≈ ₹30–60 |
| F2 3D Explainer | Short ≈ ₹60–120 · 5 min ≈ ₹400–900 |
| F3 How-it-works | Short ≈ ₹60–120 · long ≈ ₹500–1,200 |
| F4 Cosmic epic (5–8 min) | ≈ ₹2,000–4,000 (₹4,000–7,000 with true 4K + premium hero shots) |
