"""Dream Short Scout: the pure parts (durations, choosing which Shorts to watch, spotting YouTube links)."""

from workers.dream import scout


def test_iso_durations():
    assert scout.iso_seconds("PT45S") == 45
    assert scout.iso_seconds("PT1M5S") == 65
    assert scout.iso_seconds("PT1H") == 3600
    assert scout.iso_seconds("") == 0


def test_most_viewed_shorts_one_per_channel_and_nothing_long():
    vids = [
        {"url": "a", "channel": "A", "views": 900, "durationSec": 20},
        {"url": "a2", "channel": "A", "views": 800, "durationSec": 20},
        {"url": "long", "channel": "B", "views": 5000, "durationSec": 600},
        {"url": "c", "channel": "C", "views": 100, "durationSec": 30},
        {"url": "e", "channel": "E", "views": 10**9, "durationSec": 30},
        {"url": "d", "channel": "D", "views": 50, "durationSec": None},
    ]
    assert [v["url"] for v in scout.pick(vids, 5)] == ["a", "c", "e"]  # relevance order kept, long and repeat channels out
    assert [v["url"] for v in scout.pick(vids, 1)] == ["a"]


def test_youtube_links_are_found_in_any_form():
    text = ("see https://www.youtube.com/shorts/abcdefghijk and youtube.com/watch?v=ABCDEFGHIJK&t=1 "
            "or https://youtu.be/0123456789_ and not youtube.com/channel/xyz")
    assert scout.VIDEO_ID.findall(text) == ["abcdefghijk", "ABCDEFGHIJK", "0123456789_"]


def test_a_sentence_becomes_a_short_search_phrase():
    assert scout.keywords("a lonely vending machine glowing in a flower field at sunset") == "lonely vending machine flower field"
    assert scout.keywords("a lonely vending machine glowing in a flower field at sunset", 3) == "lonely vending machine"


def test_a_short_watched_before_is_reused_not_paid_for_again(monkeypatch):
    found = [{"url": "https://www.youtube.com/shorts/AAAAAAAAAAA", "title": "new title", "channel": "c", "views": 9},
             {"url": "https://www.youtube.com/shorts/BBBBBBBBBBB", "title": "b", "channel": "d", "views": 1}]
    monkeypatch.setenv("YOUTUBE_API_KEY", "x")
    monkeypatch.setattr(scout, "youtube_find", lambda *a, **k: (found, []))
    watched = []

    def fake_watch(v, q, m):
        watched.append(v["url"])
        return {"relevant": True, "shows": "fresh", "look": "", "camera": "", "shots": 3, "avgShotSec": 1.0, "music": "",
                "hook": "", "tools": "", "works": ""}, {"inputTokens": 1, "outputTokens": 1, "thinkingTokens": 0, "totalTokens": 2}

    monkeypatch.setattr(scout, "watch", fake_watch)
    known = {"AAAAAAAAAAA": {"url": "u", "title": "old", "shows": "a pier at dusk", "shots": 5, "error": None}}
    out = scout.scout({"query": "pier", "maxVideos": 2, "known": known})
    assert watched == ["https://www.youtube.com/shorts/BBBBBBBBBBB"], "only the new Short is paid for"
    assert out["videos"][0]["shows"] == "a pier at dusk" and out["videos"][0]["title"] == "new title"
    assert len(out["usage"]) == 1
