"""Creative Director image review: request building and long-video chunking, with a fake vision model."""

from types import SimpleNamespace

from workers.activities import review as R


def _shot(n, act, line=None):
    return {"n": n, "shotId": f"s{n}", "lineIndex": line, "act": act, "startSec": n * 2.0, "durationSec": 2.0,
            "description": f"shot {n}", "keyframePrompt": "p", "priority": "standard", "imageUri": f"s3://b/{n}.png"}


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(contents)
        shown = [int(p.split("|")[0].split()[-1]) for p in contents if isinstance(p, str) and p.startswith("\nShot ")]
        parsed = R.CreativeReview(
            video=R.VideoCritique(arc=4, hook=3 if len(self.calls) == 2 else 5, ending=4, variety=4, summary="", problem=""),
            acts=[R.ActCritique(act="A", flow=4, keyMoment=4, continuity=4, mood=4, summary="", problem="", shotsToFix=[])],
            shots=[R.ShotCritique(shot=n, meaning=4, accuracy=5, continuity=4, literal=False, problem="", fixPrompt="")
                   for n in shown])
        return SimpleNamespace(parsed=parsed, text="", usage_metadata=SimpleNamespace(
            prompt_token_count=1000, candidates_token_count=200, thoughts_token_count=0, total_token_count=1200))


def _fake(monkeypatch):
    models = FakeModels()
    monkeypatch.setattr(R, "gemini", lambda: SimpleNamespace(models=models))
    monkeypatch.setattr(R, "_small_jpeg", lambda uri, tmp, n: b"\xff\xd8jpeg")
    return models


def test_every_image_is_sent_with_its_line_paragraph_and_the_rubric(monkeypatch):
    models = _fake(monkeypatch)
    req = {"model": "vision-a", "rubric": "Judge MEANING, not words.", "title": "Night sky", "aspectRatio": "9:16",
           "lines": [{"index": 0, "act": "Paradox", "text": "Why is the night sky dark?", "startSec": 0.4}],
           "shots": [_shot(1, "Paradox", 0), _shot(2, "Paradox")]}
    out = R.review_images(req)
    sent = models.calls[0]
    text = "\n".join(p for p in sent if isinstance(p, str))
    assert "Judge MEANING, not words." in text
    assert "## Paradox" in text and "Why is the night sky dark?" in text
    assert 'heard: line 0: "Why is the night sky dark?"' in text
    assert "heard: no narration" in text
    assert sum(1 for p in sent if not isinstance(p, str)) == 2, "one image per shot"
    assert [s["shot"] for s in out["review"]["shots"]] == [1, 2]
    assert out["model"] == "vision-a" and out["imagesSent"] == 2 and out["usage"]["totalTokens"] == 1200


def test_long_videos_are_reviewed_per_paragraph_and_keep_the_weakest_overall_view(monkeypatch):
    models = _fake(monkeypatch)
    monkeypatch.setattr(R, "MAX_IMAGES_PER_CALL", 3)
    shots = [_shot(n, "Act 1" if n <= 3 else "Act 2") for n in range(1, 6)]
    out = R.review_images({"model": "m", "lines": [], "shots": shots})
    assert len(models.calls) == 2
    assert [s["shot"] for s in out["review"]["shots"]] == [1, 2, 3, 4, 5]
    assert out["review"]["video"]["hook"] == 3, "the weaker whole-video view wins"
    assert out["usage"]["totalTokens"] == 2400


def test_nothing_to_review(monkeypatch):
    _fake(monkeypatch)
    assert R.review_images({"shots": []})["review"] is None
