from src.models import Summary
from src.summarize import Summarizer


def test_parse_fenced_json():
    text = '```json\n{"items":[{"id":"1","summary":"Kısa özet"}]}\n```'
    result = Summarizer._parse_response(text, {"1"})
    assert result == {"1": Summary(text="Kısa özet")}


def test_parse_ignores_unknown_ids():
    text = '{"items":[{"id":"1","summary":"A"},{"id":"x","summary":"B"}]}'
    result = Summarizer._parse_response(text, {"1"})
    assert set(result) == {"1"}
    assert result["1"].text == "A"


def test_parse_accepts_bare_list():
    text = '[{"id":"2","summary":"Bare liste"}]'
    result = Summarizer._parse_response(text, {"2"})
    assert result["2"].text == "Bare liste"


def test_parse_structured_analyst_fields():
    text = """{"items":[{"id":"1","summary":"Not indirildi.",
    "materiality":"critical","category":"Kredi notu",
    "red_flags":["Temerrüt riski"],"analysis_points":["Nakit akışını izle"]}]}"""
    result = Summarizer._parse_response(text, {"1"})
    summary = result["1"]
    assert summary.materiality == "critical"
    assert summary.category == "Kredi notu"
    assert summary.red_flags == ("Temerrüt riski",)
    assert summary.analysis_points == ("Nakit akışını izle",)


def test_parse_normalizes_invalid_materiality():
    text = '{"items":[{"id":"1","summary":"A","materiality":"acil"}]}'
    result = Summarizer._parse_response(text, {"1"})
    assert result["1"].materiality == "normal"