# ADR 0002: M2a story pipeline (16 Nov 2026)

Status: accepted.

## What M2a does
Plan → **Research** (Gemini + Google Search grounding, facts with source links) → **Concept** → **Script** (Writer agent) →
**Music** (Lyria, extended with crossfades to the video length, beat-mapped with librosa) → **Shot plan** (narration is
voiced first with Gemini TTS so the Director agent times shots against real line lengths; cuts are then snapped to the
beat grid by `ShotTiming`) → **Keyframes** → **Animatic** (FFmpeg: stills + voice + ducked music, clearly labelled
"ANIMATIC DRAFT"). Later stages are still placeholders (M2b/M2c).

## Decisions
| # | Decision | Why |
|---|---|---|
| 1 | **Providers split by value:** paid Gemini API for voice (TTS), music (Lyria 3.5), research, text agents and **hero** keyframes (Nano Banana 2.1); **free Modal credits** for all other keyframes (Z-Image Turbo, Apache-2.0, on an L40S) | Owner's ₹400/month Gemini cap. Voice + music cost ≈ ₹12 per video and have no free equal; images are the bulk, so only the 10–20 % hero shots use the paid model. |
| 2 | **App-enforced hard cap** (`BudgetGuard`, `config/providers.yaml → pricing.gemini.monthlyCapInr`) | Google Cloud budgets only alert. Requests that would exceed the cap fail with a non-retryable error and the stage waits for the owner. Hero images fall back to Z-Image automatically when the remaining budget (minus a reserve for voice/music) can't cover them. |
| 3 | **A failed stage never kills a video.** It is recorded as FAILED and waits for the owner: *Retry* (optionally with notes) or *Continue without it* | A durable studio must survive provider outages, quota limits and model hiccups without losing work. |
| 4 | Text agents (Writer, Director) use **Spring AI `ChatClient` structured output** through one `Llm` helper (timeout, budget check, cost entry); **Embabel deferred** | Two agents with linear flow don't need a planning framework yet. Revisit when agents need tools/branching (M3+). |
| 5 | **Research runs in the Python worker** (google-genai SDK) | Grounding metadata (per-fact source links) is first-class in the Python SDK; Spring AI's Gemini module doesn't expose it cleanly. |
| 6 | Every generated file gets an **asset + provenance + licence** row (`AssetStore`); every paid call a **CostEntry** priced from config | HANDOVER §8 rules and §22. Licences are seeded in migration V2. |
| 7 | Dashboard loads media through **`/api/assets/{id}/content` → 302 to a 30-minute presigned SeaweedFS URL** | SeaweedFS supports HTTP range requests, so video seeking works without proxying bytes through Java. |
| 8 | Z-Image renders **a whole video's keyframes per Modal call** (batches of 8), container scales down after 2 minutes idle | Model load is ~110 s; per-image ~4 s on L40S. Auto-shutdown satisfies CLAUDE.md rule 8. |

## Prices used (Google pricing page, 16 Nov 2026, verify)
Lyria 3.5 $0.08/song · Nano Banana 2.1 $0.0336 (1K) · Gemini TTS audio out $9/M tokens (doubles 1 Jan 2027) ·
gemini-3.6-flash text estimated at $0.50/$3.00 per M tokens · Modal L40S ≈ $1.95/h (free $30/month credit).
