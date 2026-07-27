"""Signal history + track record.

Every non-cached Copilot call logs its directional read (symbol, interval,
lean, conviction, price at the time) into `signal_history`. A separate scorer
pass (hit by the scheduler or any client calling /signals/stats) resolves
outcomes honestly:

  - We compare the logged entry price with the close N candles later
    (horizon defaults to 24 periods of the signal's interval).
  - A bullish signal is "correct" if price rose, "incorrect" if it fell;
    bearish the mirror; neutral signals are logged but excluded from accuracy.

No survivorship tricks: every logged signal is scored once its horizon
elapses, and the stats endpoint reports the full breakdown (correct /
incorrect / pending) so the number can't be cherry-picked.
"""
from __future__ import annotations

import time
import uuid

from backend.journal.store import USE_PG, _conn, _q

# How many candles of the signal's own interval must elapse before scoring.
DEFAULT_HORIZON_PERIODS = 24


def init_db() -> None:
    ddl = """
        CREATE TABLE IF NOT EXISTS signal_history (
            id              TEXT PRIMARY KEY,
            symbol          TEXT NOT NULL,
            interval        TEXT NOT NULL,
            asset_class     TEXT,
            lean            TEXT NOT NULL,
            conviction      INTEGER,
            entry_price     DOUBLE PRECISION,
            horizon_periods INTEGER NOT NULL,
            outcome_price   DOUBLE PRECISION,
            outcome         TEXT,               -- correct | incorrect | flat | NULL (pending)
            resolved_at     DOUBLE PRECISION,
            created_at      DOUBLE PRECISION NOT NULL
        )
    """
    if not USE_PG:
        ddl = ddl.replace("DOUBLE PRECISION", "REAL")
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(ddl)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_signal_history_symbol "
            "ON signal_history(symbol, created_at)"
        )


def log_signal(symbol: str, interval: str, asset_class: str | None,
               lean: str | None, conviction, entry_price: float | None,
               horizon_periods: int = DEFAULT_HORIZON_PERIODS) -> str:
    """Record one copilot call. Returns the signal id."""
    sid = uuid.uuid4().hex[:12]
    with _conn() as conn:
        conn.cursor().execute(
            _q("INSERT INTO signal_history "
               "(id, symbol, interval, asset_class, lean, conviction, entry_price, "
               " horizon_periods, created_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"),
            (sid, symbol, interval, asset_class, lean or "neutral",
             int(conviction) if isinstance(conviction, (int, float)) else None,
             entry_price, int(horizon_periods), time.time()),
        )
    return sid


_INTERVAL_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "8h": 28800,
    "1d": 86400, "1w": 604800,
}


def resolve_pending() -> dict:
    """Score every signal whose horizon has elapsed. Idempotent."""
    from backend.data.providers import get_provider

    now = time.time()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, symbol, interval, lean, entry_price, horizon_periods, created_at "
            "FROM signal_history WHERE outcome IS NULL"
        )
        pending = cur.fetchall()

    resolved, still_pending, errors = 0, 0, 0
    for sid, symbol, interval, lean, entry_price, horizon, created_at in pending:
        step = _INTERVAL_SECONDS.get(interval, 3600)
        if now - float(created_at) < step * int(horizon):
            still_pending += 1
            continue
        if entry_price is None:
            # Can't score without a reference price — mark flat so we don't retry forever.
            with _conn() as conn:
                conn.cursor().execute(
                    _q("UPDATE signal_history SET outcome = 'flat', resolved_at = ? WHERE id = ?"),
                    (now, sid),
                )
            continue
        try:
            df = get_provider(symbol).fetch_klines(symbol, interval, int(horizon) + 2)
            outcome_price = float(df["close"].iloc[-1])
            move = (outcome_price - float(entry_price)) / float(entry_price)
            if abs(move) < 1e-6:
                outcome = "flat"
            elif lean == "bullish":
                outcome = "correct" if move > 0 else "incorrect"
            elif lean == "bearish":
                outcome = "correct" if move < 0 else "incorrect"
            else:
                outcome = "flat"  # neutral — logged, excluded from accuracy
            with _conn() as conn:
                conn.cursor().execute(
                    _q("UPDATE signal_history SET outcome_price = ?, outcome = ?, resolved_at = ? "
                       "WHERE id = ?"),
                    (outcome_price, outcome, now, sid),
                )
            resolved += 1
        except Exception:  # noqa: BLE001 — provider hiccup; retry next pass
            errors += 1
    return {"resolved": resolved, "pending": still_pending, "errors": errors}


def list_signals(symbol: str | None = None, limit: int = 100) -> list[dict]:
    sql = ("SELECT id, symbol, interval, asset_class, lean, conviction, entry_price, "
           "horizon_periods, outcome_price, outcome, resolved_at, created_at "
           "FROM signal_history")
    args: list = []
    if symbol:
        sql += " WHERE symbol = ?"
        args.append(symbol.upper())
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(_q(sql), args)
        return [
            {
                "id": r[0], "symbol": r[1], "interval": r[2], "asset_class": r[3],
                "lean": r[4], "conviction": r[5], "entry_price": r[6],
                "horizon_periods": r[7], "outcome_price": r[8], "outcome": r[9],
                "resolved_at": r[10], "created_at": r[11],
            }
            for r in cur.fetchall()
        ]


def stats() -> dict:
    """Track record rollup. Accuracy excludes neutral/flat/pending by design."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT outcome, COUNT(*) FROM signal_history GROUP BY outcome"
        )
        counts = {row[0]: int(row[1]) for row in cur.fetchall()}
        cur.execute(
            "SELECT symbol, lean, outcome FROM signal_history "
            "WHERE outcome IN ('correct', 'incorrect')"
        )
        rows = cur.fetchall()

    per_symbol: dict[str, dict] = {}
    for symbol, lean, outcome in rows:
        s = per_symbol.setdefault(symbol, {"correct": 0, "incorrect": 0})
        s[outcome] += 1
    for s in per_symbol.values():
        total = s["correct"] + s["incorrect"]
        s["accuracy_pct"] = round(100.0 * s["correct"] / total, 1) if total else None

    scored = counts.get("correct", 0) + counts.get("incorrect", 0)
    return {
        "total_signals": sum(counts.values()),
        "scored": scored,
        "pending": counts.get(None, 0),
        "flat_or_neutral": counts.get("flat", 0),
        "correct": counts.get("correct", 0),
        "incorrect": counts.get("incorrect", 0),
        "accuracy_pct": round(100.0 * counts.get("correct", 0) / scored, 1) if scored else None,
        "per_symbol": per_symbol,
        "note": ("Accuracy counts directional (bullish/bearish) signals only, scored "
                 f"{DEFAULT_HORIZON_PERIODS} periods after the call. Not financial advice."),
    }


# --- reflection loop -----------------------------------------------------------

# How many of the most recent SCORED directional calls to narrate in the block.
_REFLECTION_LOOKBACK = 10
_REFLECTION_RECENT_N = 3


def reflection(symbol: str) -> str | None:
    """A short, deterministic 'recent track record on this symbol' text block.

    Built straight from scored signal_history rows (NO LLM) so it stays honest and
    free, and injected into the copilot/debate prompt so the model reasons with its
    own recent history on this asset instead of in a vacuum. Returns None when there
    is no scored directional record yet (so the caller adds nothing — no noise).

    Neutral/flat calls are logged but excluded from accuracy (same rule as stats()).
    """
    sym = symbol.upper()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT lean, conviction, outcome FROM signal_history "
               "WHERE symbol = ? AND outcome IN ('correct', 'incorrect') "
               "ORDER BY created_at DESC LIMIT ?"),
            (sym, int(_REFLECTION_LOOKBACK)),
        )
        rows = cur.fetchall()
    if not rows:
        return None

    correct = sum(1 for _, _, o in rows if o == "correct")
    scored = len(rows)
    acc = round(100.0 * correct / scored)

    # Narrate the most recent few calls so the model sees the *sequence*, not just a %.
    recent = []
    for lean, conv, outcome in rows[:_REFLECTION_RECENT_N]:
        conv_txt = f" {conv}%" if isinstance(conv, int) else ""
        recent.append(f"{lean}{conv_txt} -> {outcome}")
    recent_txt = "; ".join(recent)

    # Honest framing: a strong recent record is context, a weak one is a caution.
    if acc >= 60:
        framing = "recent calls here have been mostly right"
    elif acc <= 40:
        framing = "recent calls here have been mostly WRONG — weigh this carefully"
    else:
        framing = "recent calls here have been mixed"

    return (
        f"Track record for {sym} (last {scored} scored directional calls): "
        f"{correct}/{scored} correct ({acc}%) — {framing}. "
        f"Most recent: {recent_txt}. "
        "Use this as context on your own recent reliability for this asset, not as a "
        "direction signal."
    )
