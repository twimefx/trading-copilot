"""Tests for the LLM self-correction sanitizer in the copilot analysis path.

Guards the production-text-integrity fix: mid-sentence model self-corrections
("...actually...", "no, ...", "wait no") must never reach the client, while
legitimate prose (including normal commas/dashes) is left untouched.
"""
from backend.signals.copilot import _sanitize_text, _clean_result


def test_strips_ellipsis_actually_correction():
    s = ("MACD is negative (-455.3) and below... actually above its signal "
         "(-461.3), but overall confirming downside momentum")
    out = _sanitize_text(s)
    assert "actually" not in out
    assert "and below..." not in out
    assert "above its signal (-461.3)" in out
    assert out.startswith("MACD is negative (-455.3)")


def test_strips_dash_no_correction():
    assert _sanitize_text("RSI is rising — no, falling toward oversold") == "falling toward oversold"


def test_strips_comma_no_correction():
    assert _sanitize_text("momentum is up, no, down on the day") == "down on the day"


def test_leaves_clean_text_untouched():
    clean = "RSI_14 at 39.25 sits below the 50 midline, consistent with bearish control"
    assert _sanitize_text(clean) == clean
    clean2 = "A perfectly clean sentence, with a normal comma, no corrections."
    assert _sanitize_text(clean2) == clean2


def test_idempotent():
    s = "price below... actually above the EMA20"
    assert _sanitize_text(_sanitize_text(s)) == _sanitize_text(s)


def test_handles_non_strings_and_empty():
    assert _sanitize_text("") == ""
    assert _sanitize_text(None) is None  # type: ignore[arg-type]


def test_clean_result_applies_to_all_text_fields():
    result = {
        "lean": "bearish",
        "conviction": 55,
        "summary": "Trend is up... actually down on the session.",
        "drivers": ["MACD below... actually above signal", "clean driver tied to data"],
        "risks": ["RSI rising — no, falling fast"],
        "suggested_invalidation": "a close above EMA50",
        "range_24h": {"low": 1.0, "high": 2.0, "source": "ATR estimate"},
    }
    _clean_result(result)
    assert "actually" not in result["summary"]
    assert "actually" not in result["drivers"][0]
    assert result["drivers"][1] == "clean driver tied to data"
    assert result["risks"][0] == "falling fast"
    # non-text / deterministic fields are untouched
    assert result["conviction"] == 55
    assert result["range_24h"]["source"] == "ATR estimate"
