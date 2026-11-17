"""Source check: a fact's numbers must appear on a cited page; unreachable pages are reported, never fatal."""

from workers.activities import sources as S


def test_numbers_are_extracted_and_normalised():
    assert S.key_numbers("At 4,000 m the pressure is about 400 atmospheres; 90% glow.") == ["4000", "400", "90"]


def test_facts_are_confirmed_unconfirmed_or_unreachable(monkeypatch):
    pages = {"https://a.org/x": "The midnight zone ends near 4,000 metres where pressure reaches 400 atm.",
             "https://b.org/y": "Giant squid have the largest eyes."}
    monkeypatch.setattr(S, "page_text", lambda url: pages.get(url))
    facts = [{"text": "At 4,000 m pressure is 400 atm.", "sources": [{"uri": "https://a.org/x"}]},
             {"text": "Giant squid eyes are 27 cm across.", "sources": [{"uri": "https://b.org/y"}]},
             {"text": "Anglerfish make light with bacteria.", "sources": [{"uri": "https://b.org/y"}]},
             {"text": "About 90% of animals glow.", "sources": [{"uri": "https://gone.example/z"}]}]
    assert [f["check"] for f in S.check(facts)] == ["confirmed", "unconfirmed", "no-numbers", "unreachable"]
