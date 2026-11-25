# Changing Temporal workflow code safely

Why: a running workflow is rebuilt by **replaying** its saved history against the current code. If new code makes different decisions
(a new activity, a reordered step, a renamed activity or signal), replay fails with a non-determinism error and that video/Short is stuck.
Workflows here live for hours to days (owner review windows), so there are always open ones when we deploy.

Workflow modules: `studio/workflow/video_production.py`, `dream_workflow.py`, `autopilot_workflow.py`. Guard: `studio/tests/test_workflow_determinism.py`.

## Determinism rule
Workflow code may only depend on its inputs, signals, activity results and `workflow.now()`, `workflow.uuid4()`, `workflow.random()`.
No `datetime.now`, `time.time`, `random`, `uuid4`, `open`, `os.environ`, network or DB calls; all I/O is an activity (CLAUDE.md rule 5).
Pass IDs, never media bytes. Do not iterate sets or rely on dict order from outside data for anything that schedules commands.

## What is a breaking change (needs a patch)
Anything that changes the sequence of commands an old history already contains: adding/removing/reordering `execute_activity`, timers,
`wait_condition`s, child workflows; changing an activity name, task queue, or a timer duration; branching differently on the same inputs.

Safe without a patch: editing activity bodies (not their names/signatures used by open histories), log text, adding a new signal/query
handler, changing code that only runs after a point no open workflow has reached AND no open workflow can ever reach (rare; prefer a patch).

## Steps to change workflow code
1. Make the change inside `if workflow.patched("<id>"):` (new behaviour) / `else:` (old behaviour, left untouched).
2. Name patches `<date>-<what>`, e.g. `2026-11-25-extra-review-gate`. Never reuse or rename an id.
3. Run `uv run --group dev --group studio python -m pytest studio -q -k workflow` and the replay test (below). Fix the snapshot in
   `test_workflow_determinism.py` only if you intentionally changed a name, in the same commit.
4. Deploy. New workflows take the new path; open ones keep the old path.
5. Drain: wait until no open workflow started before the deploy remains (Temporal UI http://localhost:8233, query
   `WorkflowType STARTS_WITH "VideoProduction" AND ExecutionStatus="Running" AND StartTime < "<deploy time>"`). Finish or cancel them
   (the owner decides on cancel).
6. When drained, replace the `if workflow.patched(id)` block with `workflow.deprecate_patch(id)` plus the new code only. Deploy again.
   After that is drained too, delete the `deprecate_patch` line.

Renaming a signal/query/workflow type: add the new name, keep the old handler until drained, then remove. Renaming an activity: same, via a patch
that picks the name.

Never edit a workflow just to "clean up" while videos are running. A redo that finishes a stage is not a reason to skip a patch.

## Capture a history (for replay tests)
Read-only; needs the dev server on 127.0.0.1:7233 already running. Saves into `studio/tests/fixtures/histories/<workflow-id>.json`
(the guard test replays every `*.json` there; keep it small, and do not commit histories with secrets or private prompts).

```python
# uv run --group studio python capture_history.py <workflow-id>
import asyncio, sys
from pathlib import Path
from temporalio.client import Client

async def main(wid: str) -> None:
    client = await Client.connect("127.0.0.1:7233")
    history = await client.get_workflow_handle(wid).fetch_history()
    out = Path("studio/tests/fixtures/histories"); out.mkdir(parents=True, exist_ok=True)
    (out / f"{wid}.json").write_text(history.to_json(), encoding="utf-8")

asyncio.run(main(sys.argv[1]))
```

Capture one history of each type from before a risky change (a finished short one is fine), then the replay test proves the new code still
understands it. A replay of a *completed* history only proves the path it took; patches for paths it did not take are checked by review.

## What the guard test cannot catch
Logic changes that keep the same names but change command order on a path no saved history covers; non-determinism hidden inside helpers in
other modules; dependency upgrades. Patches plus saved histories are the real protection; the test is a cheap tripwire.
