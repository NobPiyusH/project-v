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
        {"url": "d", "channel": "D", "views": 50, "durationSec": None},
    ]
    assert [v["url"] for v in scout.pick(vids, 5)] == ["a", "c"]
    assert [v["url"] for v in scout.pick(vids, 1)] == ["a"]


def test_youtube_links_are_found_in_any_form():
    text = ("see https://www.youtube.com/shorts/abcdefghijk and youtube.com/watch?v=ABCDEFGHIJK&t=1 "
            "or https://youtu.be/0123456789_ and not youtube.com/channel/xyz")
    assert scout.VIDEO_ID.findall(text) == ["abcdefghijk", "ABCDEFGHIJK", "0123456789_"]
