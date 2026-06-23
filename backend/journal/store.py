"""Trade Journal persistence — Postgres in prod, SQLite for local dev.

Backend is auto-selected at runtime:
  * DATABASE_URL set  -> Postgres (via psycopg 3). Durable, survives redeploys,
    safe across multiple instances. This is what Railway injects when you add a
    Postgres database to the project.
  * DATABASE_URL unset -> stdlib sqlite3 file at JOURNAL_DB_PATH (default
    "journal.db"). Zero-setup for local dev and tests.

The two dialects differ in only two mechanical ways, which we abstract:
  1. Parameter placeholder: sqlite uses "?", Postgres uses "%s".
  2. Connection construction + the analysis-blob column type (TEXT vs JSONB).

All CRUD logic is shared. The store interface is deliberately small so callers
never care which backend is live.

Scoping: there's no auth yet, so entries are owned by a client-generated
`owner_id` (a UUID the browser keeps and sends as a header). This upgrades
cleanly to authenticated user ids — owner_id just becomes the user id.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

# --- backend selection -------------------------------------------------------
_RAW_DB_URL = os.environ.get("DATABASE_URL", "").strip()
# Railway/Heroku sometimes hand out the older "postgres" scheme; psycopg 3 wants
# the "postgresql" scheme. Normalize the scheme prefix so either form works.
_OLD_SCHEME = "postgres" + "://"
_NEW_SCHEME = "postgresql" + "://"
if _RAW_DB_URL.startswith(_OLD_SCHEME) and not _RAW_DB_URL.startswith(_NEW_SCHEME):
    DATABASE_URL = _NEW_SCHEME + _RAW_DB_URL[len(_OLD_SCHEME):]
else:
    DATABASE_URL = _RAW_DB_URL

USE_PG = bool(DATABASE_URL)
PH = "%s"  # Postgres placeholder; SQLite uses "?" (translated in _q)

# SQLite fallback path (local dev / tests). Env-overridable.
DB_PATH = os.environ.get("JOURNAL_DB_PATH", "journal.db")

# Allowed lifecycle values.
VALID_STATUS = {"idea", "open", "closed", "cancelled"}
VALID_DIRECTION = {"long", "short", "none"}
VALID_OUTCOME = {"win", "loss", "breakeven", None}


@contextmanager
def _conn() -> Iterator[Any]:
    """Yield a connection for the active backend, committing on clean exit."""
    if USE_PG:
        import psycopg  # lazy import so dev without psycopg still works

        conn = psycopg.connect(DATABASE_URL, autocommit=False)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _q(sql: str) -> str:
    """Translate the canonical "?" placeholders to the active dialect."""
    return sql.replace("?", PH) if USE_PG else sql


def init_db() -> None:
    """Create the schema if absent. Idempotent — safe to call on every boot."""
    # JSONB on Postgres (queryable, validated), TEXT on SQLite.
    analysis_type = "JSONB" if USE_PG else "TEXT"
    ddl = f"""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id              TEXT PRIMARY KEY,
            owner_id        TEXT NOT NULL,
            created_at      DOUBLE PRECISION NOT NULL,
            updated_at      DOUBLE PRECISION NOT NULL,

            symbol          TEXT NOT NULL,
            interval        TEXT,
            lean            TEXT,
            conviction      INTEGER,
            summary         TEXT,
            range_low       DOUBLE PRECISION,
            range_high      DOUBLE PRECISION,
            range_source    TEXT,
            analysis_json   {analysis_type},

            status          TEXT NOT NULL DEFAULT 'idea',
            direction       TEXT DEFAULT 'none',
            entry_price     DOUBLE PRECISION,
            exit_price      DOUBLE PRECISION,
            size            DOUBLE PRECISION,
            stop_price      DOUBLE PRECISION,
            target_price    DOUBLE PRECISION,
            outcome         TEXT,
            pnl             DOUBLE PRECISION,
            notes           TEXT
        )
    """
    if not USE_PG:
        # SQLite is loosely typed; REAL stands in for DOUBLE PRECISION.
        ddl = ddl.replace("DOUBLE PRECISION", "REAL")
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(ddl)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_owner "
            "ON journal_entries(owner_id, created_at DESC)"
        )


# Columns the user is allowed to update after creation.
_EDITABLE = {
    "status", "direction", "entry_price", "exit_price", "size",
    "stop_price", "target_price", "outcome", "pnl", "notes",
}

# Canonical column order so we can map rows positionally on BOTH backends
# (psycopg returns plain tuples; sqlite Rows are also index-addressable).
_COLS = [
    "id", "owner_id", "created_at", "updated_at",
    "symbol", "interval", "lean", "conviction", "summary",
    "range_low", "range_high", "range_source", "analysis_json",
    "status", "direction", "entry_price", "exit_price", "size",
    "stop_price", "target_price", "outcome", "pnl", "notes",
]
_COL_LIST = ", ".join(_COLS)


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Map a positional row to the public dict shape."""
    d = {col: row[i] for i, col in enumerate(_COLS)}
    aj = d.pop("analysis_json", None)
    if isinstance(aj, str):
        d["analysis"] = json.loads(aj) if aj else None
    else:
        d["analysis"] = aj  # Postgres JSONB already decoded to dict/None
    d["range_24h"] = {
        "low": d.pop("range_low", None),
        "high": d.pop("range_high", None),
        "source": d.pop("range_source", None),
    }
    return d


def _dump_analysis(analysis: dict | None) -> Any:
    """Serialize the analysis blob appropriately for the active backend."""
    if not analysis:
        return None
    if USE_PG:
        from psycopg.types.json import Jsonb
        return Jsonb(analysis)
    return json.dumps(analysis)


def create_entry(owner_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a new journal entry from a (typically Copilot) analysis + trade fields."""
    if not owner_id:
        raise ValueError("owner_id required")

    analysis = payload.get("analysis") or {}
    symbol = (payload.get("symbol") or analysis.get("symbol") or "").upper()
    if not symbol:
        raise ValueError("symbol required")

    rng = (analysis.get("range_24h") or payload.get("range_24h") or {})
    status = (payload.get("status") or "idea").lower()
    direction = (payload.get("direction") or "none").lower()
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status: {status}")
    if direction not in VALID_DIRECTION:
        raise ValueError(f"invalid direction: {direction}")

    now = time.time()
    eid = uuid.uuid4().hex
    sql = _q(
        """
        INSERT INTO journal_entries (
            id, owner_id, created_at, updated_at,
            symbol, interval, lean, conviction, summary,
            range_low, range_high, range_source, analysis_json,
            status, direction, entry_price, exit_price, size,
            stop_price, target_price, outcome, pnl, notes
        ) VALUES (?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?,?,?,?)
        """
    )
    args = (
        eid, owner_id, now, now,
        symbol,
        payload.get("interval") or analysis.get("interval"),
        analysis.get("lean"),
        analysis.get("conviction"),
        analysis.get("summary"),
        rng.get("low"), rng.get("high"), rng.get("source"),
        _dump_analysis(analysis),
        status, direction,
        payload.get("entry_price"), payload.get("exit_price"),
        payload.get("size"), payload.get("stop_price"),
        payload.get("target_price"), payload.get("outcome"),
        payload.get("pnl"), payload.get("notes"),
    )
    with _conn() as conn:
        conn.cursor().execute(sql, args)

    created = get_entry(owner_id, eid)
    assert created is not None  # just inserted; present by construction
    return created


def list_entries(owner_id: str, status: str | None = None,
                 limit: int = 200) -> list[dict[str, Any]]:
    """Return an owner's entries, newest first. Optionally filter by status."""
    sql = f"SELECT {_COL_LIST} FROM journal_entries WHERE owner_id = ?"
    args: list[Any] = [owner_id]
    if status:
        sql += " AND status = ?"
        args.append(status.lower())
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(_q(sql), args)
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def get_entry(owner_id: str, entry_id: str) -> dict[str, Any] | None:
    sql = _q(f"SELECT {_COL_LIST} FROM journal_entries WHERE owner_id = ? AND id = ?")
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (owner_id, entry_id))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def update_entry(owner_id: str, entry_id: str,
                 fields: dict[str, Any]) -> dict[str, Any] | None:
    """Update editable trade fields on an entry. Ignores unknown/snapshot fields."""
    updates = {k: v for k, v in fields.items() if k in _EDITABLE}
    if "status" in updates and updates["status"] not in VALID_STATUS:
        raise ValueError(f"invalid status: {updates['status']}")
    if "direction" in updates and updates["direction"] not in VALID_DIRECTION:
        raise ValueError(f"invalid direction: {updates['direction']}")
    if "outcome" in updates and updates["outcome"] not in VALID_OUTCOME:
        raise ValueError(f"invalid outcome: {updates['outcome']}")
    if not updates:
        return get_entry(owner_id, entry_id)

    updates["updated_at"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in updates)
    args = list(updates.values()) + [owner_id, entry_id]
    sql = _q(f"UPDATE journal_entries SET {cols} WHERE owner_id = ? AND id = ?")
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, args)
        if cur.rowcount == 0:
            return None
    return get_entry(owner_id, entry_id)


def delete_entry(owner_id: str, entry_id: str) -> bool:
    sql = _q("DELETE FROM journal_entries WHERE owner_id = ? AND id = ?")
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (owner_id, entry_id))
        return cur.rowcount > 0


def stats(owner_id: str) -> dict[str, Any]:
    """Simple performance summary across an owner's CLOSED entries."""
    sql = _q(
        "SELECT outcome, pnl FROM journal_entries "
        "WHERE owner_id = ? AND status = 'closed'"
    )
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (owner_id,))
        rows = cur.fetchall()
    total = len(rows)
    wins = sum(1 for r in rows if r[0] == "win")
    losses = sum(1 for r in rows if r[0] == "loss")
    total_pnl = sum((r[1] or 0.0) for r in rows)
    decided = wins + losses
    return {
        "closed_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / decided, 3) if decided else None,
        "total_pnl": round(total_pnl, 2),
    }
