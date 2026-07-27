"""Regression: forecast_range must accept string timestamps (JSON over the wire)."""
import pandas as pd
import pytest

from backend.signals import kronos_range


def _string_ts_df(n=40):
    ts = pd.date_range("2026-06-01", periods=n, freq="h").strftime("%Y-%m-%dT%H:%M:%S")
    return pd.DataFrame({
        "timestamps": ts,  # strings, as they arrive from JSON
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "volume": [10.0] * n, "amount": [1000.0] * n,
    })


def test_forecast_range_coerces_string_timestamps(monkeypatch):
    captured = {}

    class FakePredictor:
        def predict(self, df, x_timestamp, y_timestamp, pred_len, T, top_p,
                    sample_count, verbose):
            # The model requires datetimelike x_timestamp; capture to assert dtype.
            captured["x_ts_dtype"] = str(x_timestamp.dtype)
            n = pred_len
            return pd.DataFrame({
                "high": [105.0] * n, "low": [95.0] * n, "close": [100.0] * n,
            })

    monkeypatch.setattr(kronos_range, "_get_predictor", lambda: FakePredictor())
    out = kronos_range.forecast_range(_string_ts_df(), pred_len=4, sample_count=1)
    assert "datetime" in captured["x_ts_dtype"].lower()
    assert out["expected_band_low"] == 95.0
    assert out["expected_band_high"] == 105.0
