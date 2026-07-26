"""User + subscription + usage persistence.

Same dual-backend approach as the trade journal (Postgres in prod via DATABASE_URL,
stdlib sqlite3 for local dev/tests) — we reuse its connection helpers so there's
one place that knows how to talk to the database.

Three concerns live here:
  users        — one row per Clerk user id, carrying their subscription tier and
                 Stripe customer/subscription ids.
  usage        — per-(user, UTC-day) Copilot call counter, for daily quota gates.

A user row is created lazily on first authenticated request (default tier: free),
so there's no separate signup-sync step — Clerk owns identity, we own entitlement.
"""
from __future__ import annotations

import time

from backend.billing import FREE, VALID_TIERS
from backend.journal.store import USE_PG, _conn, _q


def init_db() -> None:
    """Create user/usage tables if absent. Idempotent — safe on every boot."""
    users_ddl = """
        CREATE TABLE IF NOT EXISTS users (
            user_id              TEXT PRIMARY KEY,
            tier                 TEXT NOT NULL DEFAULT 'free',
            stripe_customer_id   TEXT,
            stripe_subscription_id TEXT,
            created_at           DOUBLE PRECISION NOT NULL,
            updated_at           DOUBLE PRECISION NOT NULL
        )
    """
    usage_ddl = """
        CREATE TABLE IF NOT EXISTS usage_daily (
            user_id     TEXT NOT NULL,
            day         TEXT NOT NULL,
            copilot_calls INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )
    """
    watchlist_ddl = """
        CREATE TABLE IF NOT EXISTS watchlists (
            user_id     TEXT PRIMARY KEY,
            symbols     TEXT NOT NULL DEFAULT '[]',
            updated_at  DOUBLE PRECISION NOT NULL
        )
    """
    spend_ddl = """
        CREATE TABLE IF NOT EXISTS spend_log (
            id          INTEGER PRIMARY KEY,
            day         TEXT NOT NULL,
            user_id     TEXT,
            endpoint    TEXT NOT NULL,
            usd         DOUBLE PRECISION NOT NULL,
            created_at  DOUBLE PRECISION NOT NULL
        )
    """
    if not USE_PG:
        users_ddl = users_ddl.replace("DOUBLE PRECISION", "REAL")
        watchlist_ddl = watchlist_ddl.replace("DOUBLE PRECISION", "REAL")
        spend_ddl = (spend_ddl
                     .replace("DOUBLE PRECISION", "REAL")
                     .replace("INTEGER PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT"))
    else:
        spend_ddl = spend_ddl.replace(
            "INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY")
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(users_ddl)
        cur.execute(usage_ddl)
        cur.execute(watchlist_ddl)
        cur.execute(spend_ddl)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_stripe_customer "
            "ON users(stripe_customer_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_spend_log_day ON spend_log(day)"
        )


def _utc_day() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def get_or_create_user(user_id: str) -> dict:
    """Return the user row, creating it (tier=free) on first sight."""
    if not user_id:
        raise ValueError("user_id required")
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT user_id, tier, stripe_customer_id, stripe_subscription_id "
               "FROM users WHERE user_id = ?"),
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            now = time.time()
            cur.execute(
                _q("INSERT INTO users (user_id, tier, created_at, updated_at) "
                   "VALUES (?, ?, ?, ?)"),
                (user_id, FREE, now, now),
            )
            return {"user_id": user_id, "tier": FREE,
                    "stripe_customer_id": None, "stripe_subscription_id": None}
        return {"user_id": row[0], "tier": row[1],
                "stripe_customer_id": row[2], "stripe_subscription_id": row[3]}


def get_tier(user_id: str) -> str:
    return get_or_create_user(user_id)["tier"]


def set_tier(user_id: str, tier: str,
             stripe_customer_id: str | None = None,
             stripe_subscription_id: str | None = None) -> None:
    """Set a user's tier (and optionally Stripe ids). Creates the row if needed."""
    if tier not in VALID_TIERS:
        raise ValueError(f"invalid tier: {tier}")
    get_or_create_user(user_id)  # ensure row exists
    now = time.time()
    sets = ["tier = ?", "updated_at = ?"]
    args: list = [tier, now]
    if stripe_customer_id is not None:
        sets.append("stripe_customer_id = ?")
        args.append(stripe_customer_id)
    if stripe_subscription_id is not None:
        sets.append("stripe_subscription_id = ?")
        args.append(stripe_subscription_id)
    args.append(user_id)
    with _conn() as conn:
        conn.cursor().execute(
            _q(f"UPDATE users SET {', '.join(sets)} WHERE user_id = ?"), args
        )


def link_stripe_customer(user_id: str, stripe_customer_id: str) -> None:
    """Associate a Stripe customer id with a user (set at checkout creation)."""
    get_or_create_user(user_id)
    with _conn() as conn:
        conn.cursor().execute(
            _q("UPDATE users SET stripe_customer_id = ?, updated_at = ? "
               "WHERE user_id = ?"),
            (stripe_customer_id, time.time(), user_id),
        )


def find_by_stripe_customer(stripe_customer_id: str | None) -> dict | None:
    """Reverse lookup — the webhook maps a Stripe event back to our user."""
    if not stripe_customer_id:
        return None
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT user_id, tier, stripe_customer_id, stripe_subscription_id "
               "FROM users WHERE stripe_customer_id = ?"),
            (stripe_customer_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {"user_id": row[0], "tier": row[1],
            "stripe_customer_id": row[2], "stripe_subscription_id": row[3]}


# --- daily usage / quota -----------------------------------------------------
def copilot_calls_today(user_id: str) -> int:
    day = _utc_day()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT copilot_calls FROM usage_daily WHERE user_id = ? AND day = ?"),
            (user_id, day),
        )
        row = cur.fetchone()
    return int(row[0]) if row else 0


def incr_copilot_call(user_id: str) -> int:
    """Atomically increment today's Copilot counter and return the new value."""
    day = _utc_day()
    with _conn() as conn:
        cur = conn.cursor()
        if USE_PG:
            cur.execute(
                _q("INSERT INTO usage_daily (user_id, day, copilot_calls) "
                   "VALUES (?, ?, 1) "
                   "ON CONFLICT (user_id, day) DO UPDATE "
                   "SET copilot_calls = usage_daily.copilot_calls + 1 "
                   "RETURNING copilot_calls"),
                (user_id, day),
            )
            return int(cur.fetchone()[0])
        # SQLite path — upsert then read back in the same transaction.
        cur.execute(
            "INSERT INTO usage_daily (user_id, day, copilot_calls) "
            "VALUES (?, ?, 1) "
            "ON CONFLICT(user_id, day) DO UPDATE "
            "SET copilot_calls = copilot_calls + 1",
            (user_id, day),
        )
        cur.execute(
            "SELECT copilot_calls FROM usage_daily WHERE user_id = ? AND day = ?",
            (user_id, day),
        )
        return int(cur.fetchone()[0])


# --- watchlists ---------------------------------------------------------------
MAX_WATCHLIST_SYMBOLS = 50


def get_watchlist(user_id: str) -> list[str]:
    """The user's saved symbols, in saved order. Empty list if never saved."""
    import json

    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT symbols FROM watchlists WHERE user_id = ?"), (user_id,)
        )
        row = cur.fetchone()
    if not row:
        return []
    try:
        symbols = json.loads(row[0])
    except (ValueError, TypeError):
        return []
    return [str(s) for s in symbols if isinstance(s, str)]


def set_watchlist(user_id: str, symbols: list[str]) -> list[str]:
    """Replace the user's watchlist. Dedupes, upper-cases, caps at MAX."""
    import json

    seen: list[str] = []
    for s in symbols:
        sym = str(s).strip().upper()
        if sym and sym not in seen:
            seen.append(sym)
    if len(seen) > MAX_WATCHLIST_SYMBOLS:
        raise ValueError(f"Watchlist capped at {MAX_WATCHLIST_SYMBOLS} symbols.")
    get_or_create_user(user_id)
    with _conn() as conn:
        conn.cursor().execute(
            _q("INSERT INTO watchlists (user_id, symbols, updated_at) VALUES (?, ?, ?) "
               "ON CONFLICT(user_id) DO UPDATE SET symbols = excluded.symbols, "
               "updated_at = excluded.updated_at"),
            (user_id, json.dumps(seen), time.time()),
        )
    return seen


# --- persistent spend log (survives restarts; powers the weekly digest) -------

def record_spend(user_id: str | None, endpoint: str, usd: float) -> None:
    if usd <= 0:
        return
    with _conn() as conn:
        conn.cursor().execute(
            _q("INSERT INTO spend_log (day, user_id, endpoint, usd, created_at) "
               "VALUES (?, ?, ?, ?, ?)"),
            (_utc_day(), user_id, endpoint, float(usd), time.time()),
        )


def spend_by_day(days: int = 7) -> list[dict]:
    """Daily spend totals for the last N UTC days, oldest first."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT day, SUM(usd), COUNT(*) FROM spend_log "
               "GROUP BY day ORDER BY day DESC LIMIT ?"),
            (int(days),),
        )
        rows = [
            {"day": r[0], "usd": round(float(r[1]), 4), "calls": int(r[2])}
            for r in cur.fetchall()
        ]
    rows.reverse()
    return rows
