"""AI Producer review (P1): request shape, chunking, verdict combination (Gemini mocked, no spend)."""

from types import SimpleNamespace

from workers.activities import producer as P


def _fake_gemini(calls, verdicts):
    def generate_content(model, contents, config):
        calls.append(contents)
        v = verdicts[len(calls) - 1]
        return SimpleNamespace(parsed=P.Review(**v), text="", usage_metadata=None)
    return lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


def _req(items, rules=None):
    return {"videoId": "v", "stage": "NARRATION", "model": "m", "rubric": "R", "context": "C", "rules": rules or [],
            "items": items}


def test_text_items_are_judged_with_rules_and_verdict_is_returned(monkeypatch):
    calls = []
    monkeypatch.setattr(P, "gemini", _fake_gemini(calls, [
        {"verdict": "approve", "confidence": 0.9, "summary": "Good.", "redoNotes": "",
         "items": [{"id": "script", "score": 8, "ok": True, "problem": "", "fix": ""}]}]))
    monkeypatch.setattr(P, "with_models", lambda spec, call, what, default=None: ("m", call("m")))
    out = P.producer_review(_req([{"id": "script", "label": "Script", "kind": "text", "text": "1. Hi", "expect": "x"}],
                                 rules=["calm, warm narration for science"]))
    assert out["verdict"] == "approve" and out["items"][0]["score"] == 8
    header = calls[0][0]
    assert "calm, warm narration for science" in header and "owner's rules" in header


def test_many_items_are_split_and_the_strictest_verdict_wins(monkeypatch):
    calls = []
    ok = {"verdict": "approve", "confidence": 0.9, "summary": "Fine.", "redoNotes": "", "items": []}
    bad = {"verdict": "redo", "confidence": 0.6, "summary": "Line 14 is rushed.", "redoNotes": "Line 14: slower.",
           "items": [{"id": "line-13", "score": 4, "ok": False, "problem": "rushed", "fix": "slower"}]}
    monkeypatch.setattr(P, "gemini", _fake_gemini(calls, [ok, bad]))
    monkeypatch.setattr(P, "with_models", lambda spec, call, what, default=None: ("m", call("m")))
    items = [{"id": f"line-{i}", "label": f"Line {i}", "kind": "text", "text": "t", "expect": "e"}
             for i in range(P.MAX_ITEMS_PER_CALL + 2)]
    out = P.producer_review(_req(items))
    assert len(calls) == 2, "split into two calls"
    assert out["verdict"] == "redo" and out["confidence"] == 0.6 and "slower" in out["redoNotes"]
