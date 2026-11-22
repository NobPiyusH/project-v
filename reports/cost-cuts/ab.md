# A/B plan: cheaper text stages (nothing here has been run; no paid calls were made)

Rule: quality first. A saving is switched on only if the owner judges B equal to A by eye / ear. Switches: `docs/cost-cuts.md`.
Use the same topic for every pair (the television explainer prompt, 3 min, 16:9) so differences come from the setting only.

## How to run each pair (owner or CTO, one Video per arm, Review mode on the stage under test)

1. Arm A = today's settings. Arm B = one change only (below). Everything else identical, same prompt, same style preset.
2. Run Plan -> Research -> Concept -> Script -> Music -> Narration -> Shot plan on both. To stop the arms differing by luck,
   run the stage under test twice on A first: if A1 and A2 already differ as much as A and B, the stage is too noisy to judge.
3. Judge **blind**: the dashboard shows both outputs as "1" and "2"; the owner does not see which is which.
4. Note the cost of each arm from `costByStage` on the Video detail.

## Pairs, in the order worth doing (biggest saving first)

| # | Stage (purpose) | A | B | What the owner compares | Judge by | Equal means |
|---|---|---|---|---|---|---|
| 1 | DIAGRAM_SPEC (Rs ~31 per Video, largest text line) | gemini-3.6-flash | `DIAGRAM_SPEC=gemini-3.5-flash-lite` | the same 6 diagram shots drawn from each model's specs | **eye**: watch the rendered diagrams: labels readable, parts in the order the narration explains them, flows animate along the right parts, no overlap, technically correct; also the text-card fallback count (`fallback: true` in `prompt_json.diagramSpec`) | no more fallbacks, no new overlaps or wrong mechanisms |
| 2 | RESEARCH backup (Rs ~8) | claude-sonnet-5-5 | `COST_RESEARCH_BACKUP_MODEL=claude:claude-haiku-5-5` | the fact list for the same topic | **eye**: number of facts, every number sourced, no invented figures (the source check already marks unconfirmed ones: compare how many each arm has) | same count of confirmed facts, none wrong |
| 3 | CONCEPT (Rs ~2) | claude-sonnet-5-5 | `CONCEPT=claude:claude-haiku-5-5` | big idea, clock, acts | **eye**: does the idea make you feel something, do the acts add up to the length (the earlier Claude vs Gemini concept A/B scored 9 vs 7, so Gemini is already known weaker: only Haiku is worth testing) | owner would not rewrite either |
| 4 | PLAN (Rs ~1.6) | claude-sonnet-5-5 | `PLAN=text` (Gemini list) | the plan JSON: format, duration, preset, narration mode, visual types | **eye**: choices match the prompt, no `needsResearch` mistakes | identical choices on 3 different prompts |
| 5 | Split Shot plan | one call | `COST_SHOT_PLAN_SPLIT_TOKENS=4000` (forces a split on a 3 min video) | the same script's shot list | **eye on the animatic**: the join around the middle shot (continuity of colour, scale, direction), the hero shots (opening and ending), every line covered | the join is not noticeable |
| 6 | Diagram-spec cache | fresh specs | `COST_DIAGRAM_SPEC_CACHE=true` on a re-run | the second run's diagrams vs freshly asked ones | **eye**: each reused diagram still matches its (re-worded) shot description | no mismatch seen |

Not A/B-able, no quality question: `COST_REUSE_STORED_STAGES` (returns exactly what was already approved). The owner only has to say "turn it on".

SCRIPT and SHOT_PLAN stay on Sonnet at medium effort: they are the quality core and are not in this plan.

## Verdict sheet (fill per pair)

| Pair | Prefers | Notes (what looked / read worse) | Switch on? |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | | | |
| 6 | | | |
