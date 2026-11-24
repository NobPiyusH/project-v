"""The write-replay comparison itself (studio.contract.writes): forms ignore the owner's words and the digits, and see real structural drift."""

from studio.contract.writes import drift, form, merge


def test_form_hides_values_but_keeps_structure():
    a = form({"id": "8cc3ae05-5598-4140-a5ba-47916316d323", "n": 3, "t": "two words", "x": None, "m": {"k": [1]}})
    assert a == {"id": "uuid", "m": {"k": ["num"]}, "n": "num", "t": "text", "x": "null"}


def test_drift_finds_missing_and_changed_columns_and_ignores_nulls_and_enum_words():
    java = form({"a": 1, "b": {"c": "AUTO"}, "d": None})
    assert drift(java, form({"a": 2, "b": {"c": "REVIEW"}, "d": 5})) == []
    out = drift(java, form({"a": "two words", "b": {"c": "AUTO", "e": 1}}))
    assert ".a: Java 'num', here 'text'" in out and ".b.e: extra here" in out and ".d: missing here" in out


def test_merge_fills_nulls_from_other_rows():
    assert merge(form({"a": None}), form({"a": 1})) == {"a": "num"}
