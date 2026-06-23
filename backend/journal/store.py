"""Trade Journal persistence — stdlib sqlite3, zero new prod dependency.

Why SQLite: the MVP runs as a single Railway instance (same assumption as the
in-memory guards). A file-backed SQLite DB on a persistent volume gives durable
storage with no extra service to provision or pay for. The store interface is
deliberately small so we can swap in Postgres when we scale horizontally.

Scoping: there's no auth yet, so entries are owned by a client-generated
`owner_id` (a UUID the browser keeps in localStorage and sends as a header).
This is honest about the current trust model and upgrades cleanly to real user
IDs once an auth gate lands — `owner_id` just becomes the authenticated user id.

Concurrency: SQLite with WAL mode + a short busy timeout handles the low write
volume of a journal fine. Each call opens its own connection (thread-safe).
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from typing import Any

# Env-overridable so prod points at a persistent volume (e.g. /data/journal.db)
# without a code change. Defaults to a local file for dev.
DB_PATH = os.environ.get("JOURNAL_DB_PATH", "journal.db")

# Allowed lifecycle states for a journal entry.
VALID_STATUS = {"idea", "open", "closed", "cancelled"}
VALID_DIRECTION = {"long", "short", "none"}
VALID_OUTCOME = {"win", "loss", "breakeven", None}


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db() -> None:
    """Create the schema if it doesn't exist. Safe to call repeatedly (idempotent)."""
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS journal_entries (
                id              TEXT PRIMARY KEY,
                owner_id        TEXT NOT NULL,
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL,

                -- Saved analysis snapshot (immutable record of what the Copilot said)
                symbol          TEXT NOT NULL,
                interval        TEXT,
                lean            TEXT,
                conviction      INTEGER,
                summary         TEXT,
                range_low       REAL,
                range_high      REAL,
                range_source    TEXT,
                analysis_json   TEXT,          -- full analysis blob for fidelity

                -- The user's own trade record
                status          TEXT NOT NULL DEFAULT 'idea',
                direction       TEXT DEFAULT 'none',
                entry_price     REAL,
                exit_price      REAL,
                size            REAL,
                stop_price      REAL,
                target_price    REAL,
                outcome         TEXT,
                pnl             REAL,
                notes           TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_journal_owner "
            "ON journal_entries(owner_id, created_at DESC)"
        )


# Columns the user is allowed to update after creation.
_EDITABLE = {
    "status", "direction", "entry_price", "exit_price", "size",
    "stop_price", "target_price", "outcome", "pnl", "notes",
}


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    # Expand the analysis snapshot into a nested object for the client.
    aj = d.pop("analysis_json", None)
    d["analysis"] = json.loads(aj) if aj else None
    d["range_24h"] = {
        "low": d.pop("range_low", None),
        "high": d.pop("range_high", None),
        "source": d.pop("range_source", None),
    }
    return d


def create_entry(owner_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a new journal entry from a (typically Copilot) analysis + trade fields.

    `payload` may carry a full `analysis` dict (the Copilot result) and/or explicit
    trade fields. We extract the snapshot columns for cheap querying and keep the
    full analysis blob for fidelity.
    """
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
    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO journal_entries (
                id, owner_id, created_at, updated_at,
                symbol, interval, lean, conviction, summary,
                range_low, range_high, range_source, analysis_json,
                status, direction, entry_price, exit_price, size,
                stop_price, target_price, outcome, pnl, notes
            ) VALUES (?,?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?,?,?,?)
            """,
            (
                eid, owner_id, now, now,
                symbol,
                payload.get("interval") or analysis.get("interval"),
                analysis.get("lean"),
                analysis.get("conviction"),
                analysis.get("summary"),
                rng.get("low"), rng.get("high"), rng.get("source"),
                json.dumps(analysis) if analysis else None,
                status, direction,
                payload.get("entry_price"), payload.get("exit_price"),
                payload.get("size"), payload.get("stop_price"),
                payload.get("target_price"), payload.get("outcome"),
                payload.get("pnl"), payload.get("notes"),
            ),
        )
    created = get_entry(owner_id, eid)
    assert created is not None  # just inserted; present by construction
    return created


def list_entries(owner_id: str, status: str | None = None,
                 limit: int = 200) -> list[dict[str, Any]]:
    """Return an owner's entries, newest first. Optionally filter by status."""
    q = "SELECT * FROM journal_entries WHERE owner_id = ?"
    args: list[Any] = [owner_id]
    if status:
        q += " AND status = ?"
        args.append(status.lower())
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    with _conn() as conn:
        rows = conn.execute(q, args).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_entry(owner_id: str, entry_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM journal_entries WHERE owner_id = ? AND id = ?",
            (owner_id, entry_id),
        ).fetchone()
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
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE journal_entries SET {cols} WHERE owner_id = ? AND id = ?",
            args,
        )
        if cur.rowcount == 0:
            return None
    return get_entry(owner_id, entry_id)


def delete_entry(owner_id: str, entry_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM journal_entries WHERE owner_id = ? AND id = ?",
            (owner_id, entry_id),
        )
        return cur.rowcount > 0


def stats(owner_id: str) -> dict[str, Any]:
    """Simple performance summary across an owner's CLOSED entries."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT outcome, pnl FROM journal_entries "
            "WHERE owner_id = ? AND status = 'closed'",
            (owner_id,),
        ).fetchall()
    total = len(rows)
    wins = sum(1 for r in rows if r["outcome"] == "win")
    losses = sum(1 for r in rows if r["outcome"] == "loss")
    total_pnl = sum((r["pnl"] or 0.0) for r in rows)
    decided = wins + losses
    return {
        "closed_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / decided, 3) if decided else None,
        "total_pnl": round(total_pnl, 2),
    }
