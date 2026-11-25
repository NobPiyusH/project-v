"""Cheap guards against breaking running Temporal workflows. No server, no network. Runbook: docs/runbooks/temporal-versioning.md

1. Snapshot of every workflow's type name, run method, signals, queries, updates and the activity names it schedules. Changing any of them
   renames something that open workflows (or the API/dashboard that signals them) rely on. If the change is intended, follow the runbook,
   then update the snapshot in this file in the same commit.
2. AST check: workflow code (classes decorated @workflow.defn plus module-level helper functions of the workflow modules) must not use clocks,
   randomness, env, files or network clients directly. Activity-side classes in the same file are not checked.
3. Replay: any history JSON saved in studio/tests/fixtures/histories/ is replayed against the current code (see the runbook for capture).
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
from pathlib import Path

import pytest

RUNBOOK = "docs/runbooks/temporal-versioning.md"
MODULES = ["studio.workflow.autopilot_workflow", "studio.workflow.dream_workflow", "studio.workflow.video_production"]
HISTORIES = Path(__file__).parent / "fixtures" / "histories"

EXPECTED = {
    "PostWorkflowPy": ("post", [], [], []),
    "DreamShortWorkflowPy": ("run", ["approve", "cancel", "redo", "retry", "set_mode"], ["view"], []),
    "VideoProductionWorkflowPy": (
        "run", ["approve", "pause", "reject", "resume", "set_all_modes", "set_mode", "set_review_minutes"], ["status"], []),
    "ShotRedoWorkflowPy": ("run", [], [], []),
    "ClipRedoWorkflowPy": ("run", [], [], []),
    "VoiceRedoWorkflowPy": ("run", [], [], []),
    "FootageWorkflowPy": ("run", [], [], []),
    "CreativeReviewWorkflowPy": ("run", [], [], []),
}

FORBIDDEN_CALLS = {
    "datetime.now", "datetime.utcnow", "datetime.today", "datetime.datetime.now", "datetime.datetime.utcnow",
    "time.time", "time.monotonic", "time.sleep", "time.perf_counter",
    "uuid.uuid1", "uuid.uuid4", "os.getenv", "os.urandom", "open", "input", "print",
    "secrets.token_hex", "secrets.token_urlsafe",
}
FORBIDDEN_PREFIXES = ("random.", "os.environ", "subprocess.", "requests.", "httpx.", "boto3.", "sqlalchemy.", "psycopg")
FORBIDDEN_IMPORTS = {"requests", "httpx", "boto3", "sqlalchemy", "psycopg", "psycopg2", "random", "subprocess"}


def _dotted(node: ast.AST) -> str:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _is_workflow_defn(cls: ast.ClassDef) -> bool:
    for d in cls.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        if _dotted(target) == "workflow.defn":
            return True
    return False


def _workflow_code(tree: ast.Module):
    """The nodes that run inside the workflow sandbox: defn classes and module-level functions."""
    for node in tree.body:
        is_defn = isinstance(node, ast.ClassDef) and _is_workflow_defn(node)
        if is_defn or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _violations(source: str) -> list[str]:
    tree = ast.parse(source)
    found: list[str] = []
    for node in tree.body:  # module-level imports must be sandbox-safe: nothing network/random/db at top level
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module.split(".")[0]]
        found += [f"line {node.lineno}: module-level import of {n}" for n in names if n in FORBIDDEN_IMPORTS]
    for root in _workflow_code(tree):
        for n in ast.walk(root):
            if isinstance(n, ast.Call):
                name = _dotted(n.func)
                if name in FORBIDDEN_CALLS or name.startswith(FORBIDDEN_PREFIXES):
                    found.append(f"line {n.lineno}: {name}()")
            elif isinstance(n, ast.Attribute) and _dotted(n).startswith("os.environ"):
                found.append(f"line {n.lineno}: os.environ")
    return sorted(set(found))


def _activity_names(source: str) -> set[str]:
    names = set()
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Call) and _dotted(n.func) in ("workflow.execute_activity", "workflow.start_activity") and n.args:
            a = n.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                names.add(a.value)
    return names


def _definitions() -> dict[str, tuple[str, list[str], list[str], list[str]]]:
    out = {}
    for mod in MODULES:
        m = importlib.import_module(mod)
        for c in vars(m).values():
            d = getattr(c, "__temporal_workflow_definition", None) if isinstance(c, type) else None
            if d is not None and c.__module__ == mod:
                out[d.name] = (d.run_fn.__name__, sorted(d.signals), sorted(d.queries), sorted(d.updates))
    return out


def test_workflow_definitions_match_snapshot():
    assert _definitions() == EXPECTED, (
        "Workflow type/run/signal/query/update names changed. Open workflows and the API depend on them. "
        f"Read {RUNBOOK} (renaming = new name + keep the old handler until drained), then update EXPECTED in this test."
    )


@pytest.mark.parametrize("mod", MODULES)
def test_no_nondeterminism_in_workflow_code(mod):
    bad = _violations(inspect.getsource(importlib.import_module(mod)))
    assert not bad, f"Non-deterministic code in {mod} (see {RUNBOOK}, 'Determinism'):\n  " + "\n  ".join(bad)


def test_static_check_catches_known_bad_code():
    bad = (
        "import random\nfrom temporalio import workflow\nimport datetime\n"
        "@workflow.defn\nclass W:\n    @workflow.run\n    async def run(self):\n"
        "        a = datetime.datetime.now()\n        b = uuid.uuid4()\n        c = os.environ['X']\n        d = open('f')\n"
    )
    assert len(_violations(bad)) >= 4, _violations(bad)


def test_activity_names_snapshot():
    """Scheduled activity names are part of the history: renaming one breaks replay of open workflows."""
    names = set()
    for mod in MODULES:
        names |= _activity_names(inspect.getsource(importlib.import_module(mod)))
    assert {"autopilot.upload_request", "autopilot.save_upload", "youtube_upload"} <= names, (
        f"A scheduled activity name was renamed or removed. See {RUNBOOK} before changing it."
    )


@pytest.mark.skipif(not list(HISTORIES.glob("*.json")), reason="no saved histories (runbook: 'Capture a history')")
def test_saved_histories_replay():
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    from studio.workflow import autopilot_workflow, dream_workflow, video_production

    workflows = autopilot_workflow.WORKFLOWS + dream_workflow.WORKFLOWS + video_production.WORKFLOWS

    async def run() -> None:
        histories = [WorkflowHistory.from_json(p.stem, p.read_text(encoding="utf-8")) for p in sorted(HISTORIES.glob("*.json"))]
        await Replayer(workflows=workflows).replay_workflows(histories)

    try:
        asyncio.run(run())
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"Replay of a saved history failed: {e}\nThe workflow change is not backward compatible. See {RUNBOOK}.")
