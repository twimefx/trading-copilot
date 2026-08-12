"""Tests for deterministic, explainable KRONOS consensus scoring."""
from backend.signals.context import MarketContext
from backend.signals.kronos_consensus import score_context


def test_consensus_returns_auditable_component_scores_for_equity_context():
    context = MarketContext(
        symbol="NVDA", interval="1d", asset_class="equity",
        indicators={
            "last_close": 123.45, "rsi_14": 58.0, "macd_hist": 0.42,
            "ema_20": 120.0, "ema_50": 118.0, "sma_200": 105.0,
            "atr_pct": 2.1, "volume_trend": 1.2,
        },
        funding={"available": False, "note": "n/a for equities"},
        open_interest={"available": False, "note": "n/a for equities"},
        structure={"available": True, "structure": "uptrend (higher highs & higher lows)"},
    )

    result = score_context(context)

    assert result["signal"] in {"STRONG BUY", "BUY", "MODERATE BUY", "NEUTRAL", "WAIT", "MODERATE SELL", "SELL", "STRONG SELL"}
    assert result["asset_class"] == "equity"
    assert result["overall_score"] > 50
    assert result["consensus_confidence"] > 0
    assert result["model_probability"] is not None
    assert {component["component_type"] for component in result["components"]} >= {"technical", "quant", "regime", "risk"}
    assert all(component["evidence"] for component in result["components"])


def test_consensus_is_reproducible_and_uses_context_as_of():
    context = MarketContext(
        symbol="NVDA", interval="1d", asset_class="equity",
        indicators={"last_close": 123.45, "rsi_14": 58.0, "atr_pct": 2.1},
        structure={"available": True, "structure": "range/mixed structure"},
        provenance={"as_of": "2026-08-11T20:00:00+00:00"},
    )

    first = score_context(context)
    second = score_context(context)

    assert first == second
    assert first["as_of"] == "2026-08-11T20:00:00+00:00"
    assert all(component["as_of"] == first["as_of"] for component in first["components"])
