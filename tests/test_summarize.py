from src.summarize import Summarizer


def test_parse_fenced_json():
    text = '```json\n{"items":[{"id":"1","summary":"Kısa özet"}]}\n```'
    assert Summarizer._parse_response(text, {"1"}) == {"1": "Kısa özet"}


def test_parse_ignores_unknown_ids():
    text = '{"items":[{"id":"1","summary":"A"},{"id":"x","summary":"B"}]}'
    assert Summarizer._parse_response(text, {"1"}) == {"1": "A"}


def test_parse_accepts_bare_list():
    text = '[{"id":"2","summary":"Bare liste"}]'
    assert Summarizer._parse_response(text, {"2"}) == {"2": "Bare liste"}