"""Tests for data provider routing (no network)."""
from backend.data import binance, oanda
from backend.data.providers import asset_class, get_provider


def test_crypto_detection():
    assert asset_class("BTCUSDT") == "crypto"
    assert asset_class("ETHUSDT") == "crypto"
    assert asset_class("SOLUSDC") == "crypto"


def test_forex_detection():
    assert asset_class("EUR_USD") == "forex"
    assert asset_class("EURUSD") == "forex"
    assert asset_class("GBP_JPY") == "forex"
    assert asset_class("XAU_USD") == "forex"  # gold


def test_provider_routing():
    assert get_provider("BTCUSDT") is binance
    assert get_provider("EUR_USD") is oanda


def test_oanda_instrument_normalization():
    assert oanda.normalize_instrument("EURUSD") == "EUR_USD"
    assert oanda.normalize_instrument("eur-usd") == "EUR_USD"
    assert oanda.normalize_instrument("EUR_USD") == "EUR_USD"
