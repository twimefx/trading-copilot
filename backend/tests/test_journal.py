"""Trade Journal tests — store CRUD + API endpoints against a temp SQLite DB."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """Fresh journal store pointed at an isolated temp DB."""
    import backend.journal.store as s
    monkeypatch.setattr(s, "DB_PATH", str(tmp_path / "journal.db"))
    s.init_db()
    return s


@pytest.fixture()
def api(tmp_path, monkeypatch):
    """TestClient whose journal store uses an isolated temp DB."""
    import backend.journal.store as s
    monkeypatch.setattr(s, "DB_PATH", str(tmp_path / "api_journal.db"))
    s.init_db()
    from backend.api.main import app
    return TestClient(app)


SAMPLE = {
    "symbol": "btcusdt",
    "interval": "1h",
    "analysis": {
        "lean": "bullish", "conviction": 72,
        "summary": "Momentum building.",
        "range_24h": {"low": 60000, "high": 64000, "source": "ATR estimate"},
    },
    "status": "idea",
    "notes": "watching for breakout",
}


# --- store ------------------------------------------------------------------

def test_create_and_get_roundtrip(store):
    e = store.create_entry("owner1", SAMPLE)
    assert e["symbol"] == "BTCUSDT"            # normalized upper
    assert e["lean"] == "bullish"
    assert e["conviction"] == 72
    assert e["range_24h"]["source"] == "ATR estimate"
    assert e["analysis"]["summary"] == "Momentum building."
    fetched = store.get_entry("owner1", e["id"])
    assert fetched["id"] == e["id"]


def test_owner_isolation(store):
    e = store.create_entry("owner1", SAMPLE)
    # Different owner cannot see or fetch it.
    assert store.get_entry("owner2", e["id"]) is None
    assert store.list_entries("owner2") == []
    assert len(store.list_entries("owner1")) == 1


def test_update_trade_fields_and_status(store):
    e = store.create_entry("owner1", SAMPLE)
    upd = store.update_entry("owner1", e["id"], {
        "status": "closed", "direction": "long",
        "entry_price": 61000, "exit_price": 63500,
        "outcome": "win", "pnl": 2500, "notes": "took the breakout",
    })
    assert upd["status"] == "closed"
    assert upd["outcome"] == "win"
    assert upd["pnl"] == 2500
    assert upd["updated_at"] >= upd["created_at"]


def test_update_rejects_bad_status(store):
    e = store.create_entry("owner1", SAMPLE)
    with pytest.raises(ValueError):
        store.update_entry("owner1", e["id"], {"status": "bogus"})


def test_update_ignores_snapshot_fields(store):
    e = store.create_entry("owner1", SAMPLE)
    # Attempting to overwrite the immutable analysis snapshot is a no-op.
    upd = store.update_entry("owner1", e["id"], {"lean": "bearish", "symbol": "ETH"})
    assert upd["lean"] == "bullish"
    assert upd["symbol"] == "BTCUSDT"


def test_delete(store):
    e = store.create_entry("owner1", SAMPLE)
    assert store.delete_entry("owner1", e["id"]) is True
    assert store.get_entry("owner1", e["id"]) is None
    assert store.delete_entry("owner1", e["id"]) is False   # already gone


def test_stats_win_rate(store):
    for outcome, pnl in [("win", 100), ("win", 200), ("loss", -150)]:
        e = store.create_entry("owner1", {"symbol": "BTCUSDT"})
        store.update_entry("owner1", e["id"], {"status": "closed", "outcome": outcome, "pnl": pnl})
    # An open idea should NOT count toward closed stats.
    store.create_entry("owner1", {"symbol": "ETHUSDT", "status": "open"})
    st = store.stats("owner1")
    assert st["closed_trades"] == 3
    assert st["wins"] == 2 and st["losses"] == 1
    assert st["win_rate"] == round(2 / 3, 3)
    assert st["total_pnl"] == 150.0


# --- API --------------------------------------------------------------------

H = {"X-Owner-Id": "client-abc"}


def test_api_requires_owner_header(api):
    r = api.post("/journal", json=SAMPLE)         # no header
    assert r.status_code == 400


def test_api_full_lifecycle(api):
    # create
    r = api.post("/journal", json=SAMPLE, headers=H)
    assert r.status_code == 201
    eid = r.json()["id"]

    # list
    r = api.get("/journal", headers=H)
    assert r.status_code == 200
    assert len(r.json()["entries"]) == 1

    # another owner sees nothing
    r = api.get("/journal", headers={"X-Owner-Id": "someone-else"})
    assert r.json()["entries"] == []

    # update -> close as a win
    r = api.patch(f"/journal/{eid}", json={"status": "closed", "outcome": "win", "pnl": 320},
                  headers=H)
    assert r.status_code == 200 and r.json()["outcome"] == "win"

    # stats reflects it
    r = api.get("/journal/stats", headers=H)
    assert r.json()["closed_trades"] == 1 and r.json()["wins"] == 1

    # delete
    r = api.delete(f"/journal/{eid}", headers=H)
    assert r.status_code == 204
    r = api.get(f"/journal/{eid}", headers=H)
    assert r.status_code == 404


def test_api_update_missing_returns_404(api):
    r = api.patch("/journal/doesnotexist", json={"notes": "x"}, headers=H)
    assert r.status_code == 404


# --- Postgres backend (real server) -----------------------------------------
# Verifies the prod code path (psycopg + JSONB + "%s" placeholders) against a
# genuine Postgres, using the self-contained `pgserver` wheel. Skips cleanly if
# pgserver/psycopg aren't installed so the SQLite-only dev path stays green.

@pytest.fixture(scope="module")
def pg_uri():
    pgserver = pytest.importorskip("pgserver")
    pytest.importorskip("psycopg")
    import tempfile
    tmp = tempfile.mkdtemp(prefix="pgtest_journal_")
    server = pgserver.get_server(tmp)
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


@pytest.fixture()
def pg_store(pg_uri, monkeypatch):
    """Journal store wired to a real Postgres for one test, with a clean table."""
    import backend.journal.store as s
    monkeypatch.setattr(s, "DATABASE_URL", pg_uri)
    monkeypatch.setattr(s, "USE_PG", True)
    s.init_db()
    # Isolate each test: clear the table first.
    with s._conn() as conn:
        conn.cursor().execute("DELETE FROM journal_entries")
    return s


def test_pg_create_jsonb_roundtrip_and_isolation(pg_store):
    s = pg_store
    e = s.create_entry("ownerA", SAMPLE)
    assert e["symbol"] == "BTCUSDT"
    assert e["analysis"]["summary"] == "Momentum building."   # JSONB decoded to dict
    assert e["range_24h"]["source"] == "ATR estimate"
    assert s.get_entry("ownerB", e["id"]) is None              # owner isolation
    assert len(s.list_entries("ownerA")) == 1


def test_pg_update_stats_delete(pg_store):
    s = pg_store
    e = s.create_entry("ownerA", SAMPLE)
    u = s.update_entry("ownerA", e["id"], {"status": "closed", "outcome": "win", "pnl": 900})
    assert u["status"] == "closed" and u["outcome"] == "win"
    st = s.stats("ownerA")
    assert st["closed_trades"] == 1 and st["wins"] == 1 and st["total_pnl"] == 900.0
    assert s.delete_entry("ownerA", e["id"]) is True
    assert s.get_entry("ownerA", e["id"]) is None
