"""Admin panel tests — user management, funding, tier changes, gating, audit.

Two layers covered:
  1. Store logic (users.py admin helpers) directly against a throwaway SQLite DB.
  2. HTTP endpoints via TestClient, in open mode (AUTH_ENABLED=False) and with
     the real admin gate (AUTH_ENABLED=True + DB/env admin).
"""
from __future__ import annotations

import pytest

from backend.billing import users as user_store
from backend.billing import FREE, PRO, PREMIUM


# --- store layer ---------------------------------------------------------------

def test_effective_quota_adds_bonus_to_capped_plan():
    user_store.set_tier("u1", PRO)  # PRO base = 100
    assert user_store.effective_copilot_quota("u1", 100) == 100
    user_store.add_credits("u1", 25)
    assert user_store.effective_copilot_quota("u1", 100) == 125


def test_effective_quota_unlimited_ignores_bonus():
    user_store.set_tier("u2", PREMIUM)  # unlimited (-1)
    user_store.add_credits("u2", 500)
    assert user_store.effective_copilot_quota("u2", -1) == -1


def test_add_credits_clamps_at_zero():
    user_store.add_credits("u3", 10)
    assert user_store.add_credits("u3", -50) == 0  # can't go negative
    assert user_store.get_or_create_user("u3")["bonus_credits"] == 0


def test_admin_flag_and_email_note_roundtrip():
    assert user_store.is_admin("u4") is False
    user_store.set_admin("u4", True)
    user_store.set_email("u4", "twimetech@gmail.com")
    user_store.set_note("u4", "founder")
    u = user_store.get_or_create_user("u4")
    assert u["is_admin"] is True
    assert u["email"] == "twimetech@gmail.com"
    assert u["note"] == "founder"


def test_list_users_search_and_counts():
    user_store.set_tier("alice", PRO)
    user_store.set_email("alice", "alice@example.com")
    user_store.set_admin("boss", True)
    user_store.add_credits("boss", 5)
    counts = user_store.count_users()
    assert counts["total"] >= 2
    assert counts["by_tier"].get(PRO, 0) >= 1
    assert counts["admins"] >= 1
    hits = user_store.list_users(search="alice@example.com")
    assert any(h["user_id"] == "alice" for h in hits)
    assert all("alice" in h["user_id"] or "alice" in (h["email"] or "") for h in hits)


def test_reset_daily_usage():
    user_store.incr_copilot_call("u5")
    user_store.incr_copilot_call("u5")
    assert user_store.copilot_calls_today("u5") == 2
    user_store.reset_daily_usage("u5")
    assert user_store.copilot_calls_today("u5") == 0


def test_delete_user_removes_rows():
    user_store.set_tier("gone", PRO)
    user_store.set_watchlist("gone", ["BTCUSDT"])
    user_store.incr_copilot_call("gone")
    user_store.delete_user("gone")
    # Recreated as free on next sight, usage cleared, watchlist gone.
    assert user_store.get_tier("gone") == FREE
    assert user_store.copilot_calls_today("gone") == 0
    assert user_store.get_watchlist("gone") == []


def test_audit_log_records_actions():
    user_store.log_admin_action("admin1", "set_tier", "u9", "free -> premium")
    user_store.log_admin_action("admin1", "fund_credits", "u9", "delta=+100")
    events = user_store.admin_audit_log()
    assert len(events) >= 2
    assert events[0]["action"] == "fund_credits"  # newest first
    assert events[0]["target_user"] == "u9"
    assert any(e["action"] == "set_tier" for e in events)


# --- HTTP endpoints (open mode: AUTH_ENABLED=False → admin gate is a no-op) ---

def test_admin_endpoints_open_mode(client):
    # stats
    r = client.get("/admin/stats")
    assert r.status_code == 200
    assert "total" in r.json()
    # create + fund + tier + audit
    assert client.post("/admin/users", json={"user_id": "web1", "tier": "pro",
                                             "email": "w@x.com"}).status_code == 201
    r = client.post("/admin/users/web1/tier", json={"tier": "premium"})
    assert r.status_code == 200 and r.json()["tier"] == "premium"
    assert r.json()["previous"] == "pro"
    r = client.post("/admin/users/web1/credits", json={"delta": 100})
    assert r.status_code == 200 and r.json()["bonus_credits"] == 100
    r = client.get("/admin/users/web1")
    assert r.json()["tier"] == "premium" and r.json()["bonus_credits"] == 100
    r = client.post("/admin/users/web1/reset-usage")
    assert r.status_code == 200
    r = client.get("/admin/audit")
    assert any(e["action"] == "set_tier" for e in r.json()["events"])
    # delete
    assert client.delete("/admin/users/web1").status_code == 204


def test_admin_tier_rejects_invalid(client):
    client.post("/admin/users", json={"user_id": "bad1"})
    r = client.post("/admin/users/bad1/tier", json={"tier": "gold"})
    assert r.status_code == 422


# --- Real admin gate (AUTH_ENABLED=True) ----------------------------------------

@pytest.fixture
def auth_client(client, monkeypatch):
    """Turn auth on so require_admin actually checks admin status."""
    from backend.api import main as main_mod
    monkeypatch.setattr(main_mod.auth_mod, "AUTH_ENABLED", True)
    return client


def test_admin_gate_denies_non_admin(auth_client):
    # TEST_USER is not admin and not in env list -> 403.
    r = auth_client.get("/admin/users")
    assert r.status_code == 403


def test_admin_gate_allows_db_admin(auth_client):
    user_store.set_admin("user_test_123", True)  # TEST_USER becomes admin
    r = auth_client.get("/admin/users")
    assert r.status_code == 200


def test_admin_gate_allows_env_admin(auth_client, monkeypatch):
    from backend.api import main as main_mod
    monkeypatch.setattr(main_mod, "_ADMIN_IDS_ENV", {"user_test_123"})
    r = auth_client.get("/admin/users")
    assert r.status_code == 200


def test_admin_cannot_delete_self(auth_client):
    user_store.set_admin("user_test_123", True)
    r = auth_client.delete("/admin/users/user_test_123")
    assert r.status_code == 422


def test_me_exposes_admin_and_effective_quota(client):
    user_store.set_admin("user_test_123", True)
    user_store.set_tier("user_test_123", PRO)
    user_store.add_credits("user_test_123", 30)
    r = client.get("/me")
    body = r.json()
    assert body["is_admin"] is True
    assert body["bonus_credits"] == 30
    assert body["daily_copilot_quota"] == 130  # PRO 100 + 30 bonus
