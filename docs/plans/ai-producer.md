# Plan: AI Producer mode (draft for the owner's approval, 18 Nov 2026)

Owner's request: "I need an AI that manages like a manager: approves, checks the cost of the next redo, writes precise
prompts, hears the voice and does everything ... make one more mode: it should approve intelligently and learn from
mistakes." Nothing in this plan is built until the owner approves it.

## The idea in one line
A fourth stage mode, **PRODUCER**, next to Auto / Review / Manual: an AI producer judges each stage's output (it can
see images, watch clips and **listen to the voice**), approves or redoes it within a budget the owner sets, writes
sharper prompts for redos, asks the owner only when it should, and **learns from the owner's corrections**.

## Why today's Auto is not enough
- Auto only passes stages along; the Inspector checks technical things (length, black frames, loudness).
- Only keyframes get a creative review (Creative Director). Nobody listens to narration or watches clips.
- Every money step waits for the owner, even small ones.
- Nothing is remembered: the same mistake can repeat in the next video (`preference_event` exists since M1, unused).

## How PRODUCER mode works (per stage)
1. The stage runs as usual.
2. **Review:** the Producer looks at the output with a multimodal model (Gemini Flash: images, video and audio in).
   It scores it against a stage rubric and the owner's learned rules (see "Learning"), and lists problems per item
   (shot, line, take).
3. **Decide**, one of:
   - **approve:** good enough, continue;
   - **redo** specific items, with a rewritten, precise prompt or delivery note (e.g. "slower push-in, firefly in the
     lower third, no text"; "warmer, slower, hushed on the last line"), only if the redo's estimated cost fits the
     video's Producer budget and the item's redo limit;
   - **pick** the best take when there are several (hero drafts, voice takes);
   - **ask the owner:** when the decision is above the owner's "ask me above Rs X" line, a redo limit is reached, the
     Producer is unsure (low confidence), or a learned rule says "the owner always decides this".
4. **Log** every decision (what it saw, scores, choice, cost, confidence) like the router does, and show it on the
   dashboard.
5. **Report** at the end of the video: what it approved, redid, spent and why, plus what it learned.

## Money rules (owner sets per video, defaults in config)
- Producer budget per video (default: Rs 150 for a Short), on top of the existing monthly caps (never overrides them).
- "Ask me above Rs X" for any single decision (default Rs 50).
- Redo limit per item (default 2) and per stage (default 3 rounds).
- Before a redo it compares cost vs expected gain; the cheapest fix first (e.g. re-voice one line, not the script).

## What it reviews, stage by stage (first version)
| Stage | What the Producer checks |
|---|---|
| Script | Hook strength, facts used, length vs duration, does it cover what the prompt asked |
| Narration | **Listens** to each line: natural delivery, emotion matches the line, pacing, mispronunciations, read-aloud instructions |
| Keyframes | Matches the shot, composition, artefacts, text in picture, consistency across shots |
| Drafts / finals | **Watches** each clip: real motion (no near-still), matches the shot, glitches, black frames; picks the best take |
| Assembly | Watches the master: hook in the first 2 s, pacing, captions readable, audio balance, total length |

## Learning from mistakes (memory, not retraining)
- **Every owner action is a lesson:** approvals, rejections with notes, overrides of a Producer decision, edits,
  voice/take choices, "voice not good" style feedback. Stored in `preference_event` (finally used) with the context.
- **Rules:** repeated lessons are distilled into short, plain rules ("calm, warm narration for science", "never
  upbeat Puck-style delivery", "reject clips that barely move"), shown on a dashboard page where the owner can edit,
  disable or delete them. Rules go into every review prompt.
- **Examples:** before judging, the Producer looks up the owner's past decisions on similar items and follows them.
- **Agreement score:** when the owner reviews after the Producer, the dashboard shows how often they agreed. Start in
  **advise-only** (the Producer recommends, the owner decides) and switch to deciding once agreement is high (e.g. 80%+
  over 10+ decisions). The owner can switch any time.
- Later (M5): YouTube retention data becomes lessons too (where viewers left).

## Building blocks (reuse, no new frameworks)
- New `StageMode.PRODUCER`; the workflow already has the waits and redo paths (deterministic: all judging in activities).
- `ProducerAgent` (Java, Spring AI, Gemini multimodal) with stage rubrics; audio/video judging in the Python worker
  where the files are (Gemini file input).
- `producer_decision` table (like `routing_decision`) + `preference_event` for lessons + `producer_rule` for rules.
- Dashboard: per-video "Producer report", a "Rules it learned" page, agreement score, per-video budget and limits.

## Phases (each ends with a demo and the owner's OK)
1. **P1 (about 1 week):** PRODUCER mode with advise-only reviews of narration (listening) and clips (watching), the
   decision log and the report; money rules; lessons recorded from every owner action.
2. **P2 (about 1 week):** deciding mode (approve / redo with rewritten prompts / pick takes / ask), rules distilled
   from lessons + the rules page, agreement score.
3. **P3 (later, with M5):** viewer retention as lessons; script and assembly reviews deepened.

## Cost
About Rs 5-15 per video for reviews (Gemini Flash multimodal), counted under the Gemini cap and shown on the video.
Expected to save more by catching weak takes before paid finals and upscaling.

## Risks
- AI taste is imperfect, especially for voice: hence advise-only first, the agreement score, and owner override always.
- Gemini dependence: the review model goes through the router (fallback to "ask the owner" if unavailable).
- Over-redoing: hard budget and redo limits; never above the monthly caps.

## Decisions needed from the owner
1. Approve P1 + P2 as above?
2. Defaults OK: Rs 150 Producer budget per Short, ask above Rs 50, 2 redos per item?
3. Start in advise-only for the first ~10 decisions, then let it decide? (recommended)
