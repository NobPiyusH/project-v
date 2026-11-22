# Cost cuts for the text stages of a long-form Video (all OFF by default)

Owner rule (`quality-first-cost-cuts`): a saving ships **off** until an A/B shows equal quality. Every switch below defaults to
"nothing changes". Backend restart needed (all read at startup). Env names are in `.env.example`.

## What the ledger showed (television explainer, 22 Nov 2026, read-only from `cost_entry`)

| Purpose | Calls | Rs | Real cause |
|---|---|---|---|
| DIAGRAM_SPEC (gemini-3.6-flash) | 23 | 61.6 | **Not repairs.** Zero `DIAGRAM_SPEC_REPAIR` rows exist. The calls come in two batches of 13 and 10 (one per diagram shot, first attempt), each right after a SHOT_PLAN run. The Shot plan stage ran twice and the second run asked the model again for every shot. 23 / ~10-13 shots = 2.3 calls per shot, but it is 1 call per shot per *run*. |
| SHOT_PLAN (claude-sonnet-5-5) | 5 here (+3 on the abandoned duplicate) | 41.3 | The old 8,000-token ceiling cut a 26-shot plan off three times (8000 out each, ~Rs 11 apiece, all paid and thrown away); two later runs finished (8,069 and 8,357 out), i.e. the stage also ran twice. |
| MUSIC | 2 songs | 23 | A restarted/duplicate run executed MUSIC again with the music already stored. |
| Abandoned duplicate video | | ~57 | Same text stages paid a second time. |

Why the Python side is not involved: `workers/diagram/` makes **no** model calls (it validates the spec and draws with Manim);
all retries live in `DiagramSpecs` (one call + at most one repair, never retried on a model error).

Repair rate on this video: 0 of 23 first answers needed a repair. Output tokens dominate each call (4-9k tokens, mostly the
model's own thinking), so batching several shots into one call would save only the ~700-token system prompt per shot: not worth the
quality risk, so it is **not built**. The cost is in running the stage twice, which the switches below remove.

## The switches

| Env | Property | Default | What it does |
|---|---|---|---|
| `COST_DIAGRAM_SPEC_CACHE` | `projectv.cost.diagram-spec-cache` | false | A re-run Shot plan takes the (non-fallback) diagram spec an earlier run already paid for when the narration line and the clamped length (5-12 s) are the same. The Shot plan summary says "N diagram specs reused". Key = title + line + length (the description is re-worded on every re-plan, so it is not in the key). |
| `COST_REUSE_STORED_STAGES` | `projectv.cost.reuse-stored-stages` | false | In a **full run** (new run, restart, duplicate), a Research / Concept / Script / Music / Shot plan stage that already finished (AUTO_PASSED / APPROVED...) with its output rows still present is not generated again: the workflow takes the stored summary + artifact (recorded as "Reused stored result (no model call)") and the stage then passes through its normal mode (Auto / Review / Manual) and Inspector. Never reused: an owner Reject, a redo of chosen stages (`REDO_WITH`), an Inspector retry, a stage with owner notes, a stage whose upstream stage was genuinely re-made later, a stage whose latest attempt FAILED/REJECTED. Music: needs the `music_cue` row, so a Video that already has its music never pays for a second song. Versioned in the workflow (`reuse-stored-stages`), so running videos replay unchanged. |
| `COST_STAGE_MODELS` | `projectv.cost.stage-models` | empty | `PURPOSE=model;PURPOSE=model` first-choice model per text purpose: `PLAN`, `CONCEPT`, `SCRIPT`, `SHOT_PLAN`, `DIAGRAM_SPEC`, `CREATIVE_REVIEW`... `text` means the Gemini list with no Claude first. The usual fallback list still sits behind it. Example: `PLAN=text;CONCEPT=claude:claude-haiku-5-5;DIAGRAM_SPEC=gemini-3.5-flash-lite`. |
| `COST_RESEARCH_BACKUP_MODEL` | `projectv.models.research-backup` | empty (= story model) | Model of the Claude research backup. |
| `COST_SHOT_PLAN_SPLIT_TOKENS` | `projectv.cost.shot-plan-split-tokens` | 0 (never) | When the expected Shot plan output (1,500 thinking + 280 per shot, calibrated on the 26-shot run) reaches this many tokens, ask for it in two calls: the first half of the script lines (by narration seconds), then the second half, told where part 1 ended; shots are merged in order. Suggested value 12,000 (about 37 shots; the ceiling is 16,000). Avoids a cut-off plan being paid in full and repeated. |

Not touched: Claude effort per purpose already exists (`CLAUDE_EFFORT_OVERRIDES`, also off).

## Seeing where the money went

`GET /api/videos/{id}` now has `costByStage`: per stage (biggest first) its calls, total Rs and the purposes inside it
(`SHOT_PLAN` shows `SHOT_PLAN` and `DIAGRAM_SPEC` rows with their call counts, so an extra call count is visible at once).
Stage comes from the ledger note's purpose; unknown purposes are listed under `OTHER`. The raw `costs` list is unchanged.

## Expected saving per long-form Video (honest)

Measured on the television explainer's text stages (SHOT_PLAN + DIAGRAM_SPEC + MUSIC + RESEARCH backup + SCRIPT + CONCEPT + PLAN):
Rs ~129 in total (video d3e53cd1).

* With **cache + reuse + split** on, the same accidents (stage run twice, three cut-off plans, second song) cost about
  Rs 62-70: roughly **45-50% off that Video** (the duplicate Video's Rs 57 is not counted; reuse also catches that).
* On a **clean run** (nothing restarts, nothing cut off) these three switches save about 0: a single pass is ~Rs 31 diagram
  specs + ~Rs 9 shot plan + ~Rs 11 music + ~Rs 16 for the rest. The real remaining lever there is the per-stage model
  (Plan / Concept / Research backup together were Rs ~11.7 here, so even a free model saves at most ~Rs 9, about 8%) and
  DIAGRAM_SPEC (~Rs 31, the largest single text line), which is why those need the A/B below.
* So the 30-50% target holds for runs that restart or hit the old ceiling (this project's actual history); the clean-run saving
  is single-digit percent until a cheaper model passes the A/B.

## What needs an A/B before it is switched on (plan: `reports/cost-cuts/ab.md`)

* Cheaper model for Plan / Concept / Research backup / DIAGRAM_SPEC (`COST_STAGE_MODELS`): yes, quality can change.
* Diagram-spec cache: the reused spec was written against an earlier description of the shot; judge by eye that the diagram still
  matches the new shot's description (low risk, same narration line).
* Split Shot plan: judge continuity across the join (shot ~N/2 to N/2+1) and that the opening/closing hero shots are right.
* Stored-stage reuse: no quality question (it returns exactly what was already approved); needs no A/B, only the owner's go to
  switch it on. Safe to turn on first.

## Tests

`./gradlew :control-plane:test` (Docker needed for the Postgres ones): `DiagramSpecCacheTest`, `StageReuseTest`,
`VideoProductionWorkflowTest` (reuse, Reject and redo cases), `DirectorSplitTest`, `StageModelTest`, `StageCostSummaryTest`.
