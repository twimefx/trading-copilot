"""Tests for tiers, per-user quota gating, and Stripe webhook tier sync.

These exercise the monetization path end-to-end against SQLite, with auth and
Stripe verification stubbed (no live Clerk/Stripe needed).
"""
import os

import pytest
from fastapi.testclient import TestClient

import backend.signals.copilot as copilot_mod
from backend.api.guards import copilot_cache
from backend.billing import FREE, PRO, PREMIUM, get_tier
from backend.billing import users as user_store


# --- tier config -------------------------------------------------------------

def test_tier_defaults_and_features():
    free = get_tier(FREE)
    pro = get_tier(PRO)
    prem = get_tier(PREMIUM)
    assert free.daily_copilot_quota == 3
    assert prem.daily_copilot_quota == -1                 # unlimited
    assert "journal" not in free.features                 # journal is a paid perk
    assert "journal" in pro.features
    assert pro.scan_max_symbols < prem.scan_max_symbols
    assert get_tier("bogus").name == FREE                 # unknown -> free


# --- user store --------------------------------------------------------------

def test_user_created_free_and_tier_upgrade():
    user_store.get_or_create_user("u1")
    assert user_store.get_tier("u1") == FREE
    user_store.set_tier("u1", PRO, stripe_customer_id="cus_1", stripe_subscription_id="sub_1")
    assert user_store.get_tier("u1") == PRO
    found = user_store.find_by_stripe_customer("cus_1")
    assert found and found["user_id"] == "u1"


def test_daily_usage_counter():
    assert user_store.copilot_calls_today("u2") == 0
    assert user_store.incr_copilot_call("u2") == 1
    assert user_store.incr_copilot_call("u2") == 2
    assert user_store.copilot_calls_today("u2") == 2


# --- quota gate on /copilot --------------------------------------------------

def _client_as(user_id: str) -> TestClient:
    from backend.api.main import app
    from backend.api.auth import current_user_id
    app.dependency_overrides[current_user_id] = lambda: user_id
    return TestClient(app)


def test_free_tier_quota_blocks_with_402(monkeypatch):
    copilot_cache.clear()
    import backend.api.main as main_mod
    monkeypatch.setattr(main_mod.auth_mod, "AUTH_ENABLED", True)

    def fake_analyze(symbol, interval, include_kronos=True):
        return {"lean": "neutral", "conviction": 50, "cost_usd": 0.01,
                "range_24h": {"low": 1, "high": 2, "source": "ATR estimate"}}

    monkeypatch.setattr(copilot_mod, "analyze_symbol", fake_analyze)

    # Free tier = 3/day. Use a distinct symbol each call so the cache never
    # short-circuits and each call burns quota.
    from backend.api.main import app
    from backend.api.auth import current_user_id
    app.dependency_overrides[current_user_id] = lambda: "quota_user"
    c = TestClient(app)
    try:
        for i in range(3):
            r = c.post("/copilot", json={"symbol": f"SYM{i}USDT", "include_kronos": False})
            assert r.status_code == 200, r.text
        # 4th call is blocked with 402 Payment Required.
        r = c.post("/copilot", json={"symbol": "SYM99USDT", "include_kronos": False})
        assert r.status_code == 402
        assert r.json()["upgrade"] is True
    finally:
        app.dependency_overrides.clear()


def test_premium_tier_unlimited(monkeypatch):
    copilot_cache.clear()
    import backend.api.main as main_mod
    monkeypatch.setattr(main_mod.auth_mod, "AUTH_ENABLED", True)
    monkeypatch.setattr(
        copilot_mod, "analyze_symbol",
        lambda s, i, include_kronos=True: {"lean": "neutral", "conviction": 1,
                                           "cost_usd": 0.0,
                                           "range_24h": {"low": 1, "high": 2, "source": "x"}},
    )
    user_store.set_tier("prem_user", PREMIUM)
    from backend.api.main import app
    from backend.api.auth import current_user_id
    app.dependency_overrides[current_user_id] = lambda: "prem_user"
    c = TestClient(app)
    try:
        for i in range(6):  # well past the free cap
            r = c.post("/copilot", json={"symbol": f"P{i}USDT", "include_kronos": False})
            assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_me_reports_tier_and_remaining():
    user_store.set_tier("me_user", PRO)
    from backend.api.main import app
    from backend.api.auth import current_user_id
    app.dependency_overrides[current_user_id] = lambda: "me_user"
    c = TestClient(app)
    try:
        r = c.get("/me")
        assert r.status_code == 200
        body = r.json()
        assert body["tier"] == PRO
        assert body["copilot_calls_remaining"] == get_tier(PRO).daily_copilot_quota
    finally:
        app.dependency_overrides.clear()


def test_scan_capped_by_tier(monkeypatch):
    # Stub the scanner so we can inspect how many symbols reached it.
    import backend.api.main as main_mod
    monkeypatch.setattr(main_mod.auth_mod, "AUTH_ENABLED", True)
    import backend.signals.scanner as scanner_mod
    seen = {}
    monkeypatch.setattr(
        scanner_mod, "scan_watchlist",
        lambda symbols, interval: seen.update(n=len(symbols)) or [],
    )
    from backend.api.main import app
    from backend.api.auth import current_user_id
    app.dependency_overrides[current_user_id] = lambda: "scan_user"  # free tier
    c = TestClient(app)
    try:
        many = [f"S{i}USDT" for i in range(50)]
        r = c.post("/scan", json={"symbols": many, "interval": "1h"})
        assert r.status_code == 200
        assert r.json()["scan_max_symbols"] == get_tier(FREE).scan_max_symbols
        assert seen["n"] == get_tier(FREE).scan_max_symbols   # capped
    finally:
        app.dependency_overrides.clear()


# --- Stripe webhook tier sync (verification stubbed) -------------------------

def test_webhook_checkout_completed_sets_tier(monkeypatch):
    from backend.billing import stripe_billing
    event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": "wh_user",
            "customer": "cus_wh",
            "subscription": "sub_wh",
            "metadata": {"user_id": "wh_user", "tier": PRO},
        }},
    }
    summary = stripe_billing.handle_event(event)
    assert summary["tier"] == PRO
    assert user_store.get_tier("wh_user") == PRO


def test_webhook_subscription_deleted_downgrades(monkeypatch):
    from backend.billing import stripe_billing
    user_store.set_tier("cancel_user", PREMIUM, stripe_customer_id="cus_cancel")
    event = {
        "type": "customer.subscription.deleted",
        "data": {"object": {"customer": "cus_cancel", "id": "sub_x"}},
    }
    summary = stripe_billing.handle_event(event)
    assert summary["tier"] == FREE
    assert user_store.get_tier("cancel_user") == FREE


def test_webhook_subscription_updated_maps_price(monkeypatch):
    # Configure a price->tier mapping and simulate an upgrade event.
    monkeypatch.setenv("STRIPE_PRICE_PREMIUM", "price_prem_123")
    from backend.billing import stripe_billing
    user_store.set_tier("up_user", PRO, stripe_customer_id="cus_up")
    event = {
        "type": "customer.subscription.updated",
        "data": {"object": {
            "customer": "cus_up",
            "id": "sub_up",
            "status": "active",
            "items": {"data": [{"price": {"id": "price_prem_123"}}]},
        }},
    }
    summary = stripe_billing.handle_event(event)
    assert summary["tier"] == PREMIUM
    assert user_store.get_tier("up_user") == PREMIUM


def test_webhook_bad_signature_rejected(monkeypatch):
    from backend.api.main import app
    c = TestClient(app)
    # No STRIPE_WEBHOOK_SECRET configured -> verify raises -> 400.
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    r = c.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "bad"})
    assert r.status_code == 400
