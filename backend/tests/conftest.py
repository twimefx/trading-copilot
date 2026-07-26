"""Shared test fixtures.

Auth: the API now requires a verified Clerk JWT. For tests we override the
`current_user_id` dependency with a fixed test user instead of standing up a
real Clerk instance — the idiomatic FastAPI pattern. Individual tests can still
exercise real tier/quota logic because the override only supplies identity.

DB: tests run against a throwaway SQLite file (DATABASE_URL unset) so the users,
usage, and journal tables are real but disposable.
"""
from __future__ import annotations

import os
import tempfile

import pytest

# Ensure SQLite backend + a temp journal/users DB before any store import.
os.environ.pop("DATABASE_URL", None)
_TMP_DB = os.path.join(tempfile.gettempdir(), "copilot_test.db")
os.environ.setdefault("JOURNAL_DB_PATH", _TMP_DB)

TEST_USER = "user_test_123"


@pytest.fixture(autouse=True)
def _clean_db():
    """Fresh DB per test — remove the SQLite file so tables start empty."""
    for path in (os.environ["JOURNAL_DB_PATH"],
                 os.environ["JOURNAL_DB_PATH"] + "-wal",
                 os.environ["JOURNAL_DB_PATH"] + "-shm"):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    # Recreate schema.
    from backend.journal import store as journal_store
    from backend.billing import users as user_store
    from backend import alerts as alert_store
    from backend.signals import history as signal_history
    journal_store.init_db()
    user_store.init_db()
    alert_store.init_db()
    signal_history.init_db()
    # Reset shared in-memory guards so per-IP rate limits and the global daily
    # spend cap don't accumulate across tests (they're module-level singletons).
    try:
        from backend.api import guards
        guards.copilot_limiter._hits.clear()
        guards.spend_guard._spent = 0.0
        guards.copilot_cache.clear()
        guards.scan_cache.clear()
    except Exception:
        pass
    yield


@pytest.fixture
def client():
    """TestClient with the auth dependency overridden to a fixed test user."""
    from fastapi.testclient import TestClient
    from backend.api.main import app
    from backend.api.auth import current_user_id

    app.dependency_overrides[current_user_id] = lambda: TEST_USER
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
