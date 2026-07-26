"""Alert rules + notification delivery.

Two tables (same dual Postgres/SQLite backend as the journal/users stores):

  alert_rules  — one row per user-created rule. Rule shape is deliberately
                 simple and evaluable WITHOUT an LLM call (so checking alerts
                 costs nothing but market-data fetches):

                 { "kind": "price_above" | "price_below" | "scanner_lean",
                   "symbol": "BTCUSDT",
                   "value":  70000.0,                       # price rules
                   "interval": "1h",                        # scanner_lean
                   "symbols": ["BTCUSDT", ...],             # scanner_lean (<=10)
                   "lean": "bullish" | "bearish" }          # scanner_lean

  alert_events — audit log of fired alerts (what, when, what price/readings
                 triggered it, which channels were used).

Delivery channels (all optional, env-configured, fail-open so a bad channel
never blocks rule evaluation):
  - Telegram: ALERT_TELEGRAM_BOT_TOKEN + per-user rule chat_id (or
    ALERT_TELEGRAM_DEFAULT_CHAT_ID for rules without one)
  - Email:    ALERT_SMTP_URL=smtp://user:pass@host:port + ALERT_EMAIL_FROM,
              per-rule "email" recipient

The evaluator (`evaluate_rules`) is called by an external scheduler
(Hermes cron, a cron box, or a `while sleep` worker) hitting POST
/alerts/check — keeping the web process stateless.
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from backend.journal.store import USE_PG, _conn, _q

logger = logging.getLogger("copilot.alerts")

MAX_RULES_PER_USER = 25
SCANNER_RULE_MAX_SYMBOLS = 10

PRICE_KINDS = {"price_above", "price_below"}
SCANNER_KINDS = {"scanner_lean"}
VALID_KINDS = PRICE_KINDS | SCANNER_KINDS
VALID_LEANS = {"bullish", "bearish"}


def init_db() -> None:
    rules_ddl = """
        CREATE TABLE IF NOT EXISTS alert_rules (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            kind        TEXT NOT NULL,
            config      TEXT NOT NULL,          -- JSON rule shape (see module docstring)
            active      INTEGER NOT NULL DEFAULT 1,
            cooldown_s  INTEGER NOT NULL DEFAULT 3600,
            last_fired_at DOUBLE PRECISION,
            created_at  DOUBLE PRECISION NOT NULL,
            updated_at  DOUBLE PRECISION NOT NULL
        )
    """
    events_ddl = """
        CREATE TABLE IF NOT EXISTS alert_events (
            id          TEXT PRIMARY KEY,
            rule_id     TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            symbol      TEXT,
            message     TEXT NOT NULL,
            readings    TEXT,                   -- JSON snapshot of what triggered it
            channels    TEXT,                   -- JSON list of channels attempted
            created_at  DOUBLE PRECISION NOT NULL
        )
    """
    if not USE_PG:
        rules_ddl = rules_ddl.replace("DOUBLE PRECISION", "REAL")
        events_ddl = events_ddl.replace("DOUBLE PRECISION", "REAL")
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(rules_ddl)
        cur.execute(events_ddl)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_rules_user ON alert_rules(user_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_alert_events_rule ON alert_events(rule_id)"
        )


def validate_rule(kind: str, config: dict) -> str | None:
    """Return an error string if the rule is malformed, else None."""
    if kind not in VALID_KINDS:
        return f"kind must be one of {sorted(VALID_KINDS)}"
    if kind in PRICE_KINDS:
        symbol = (config.get("symbol") or "").strip().upper()
        if not symbol:
            return "config.symbol is required"
        value = config.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return "config.value must be a number (price threshold)"
    if kind in SCANNER_KINDS:
        symbols = config.get("symbols")
        if not isinstance(symbols, list) or not symbols:
            return "config.symbols must be a non-empty list"
        if len(symbols) > SCANNER_RULE_MAX_SYMBOLS:
            return f"config.symbols capped at {SCANNER_RULE_MAX_SYMBOLS} per rule"
        if config.get("lean") not in VALID_LEANS:
            return f"config.lean must be one of {sorted(VALID_LEANS)}"
    return None


def create_rule(user_id: str, kind: str, config: dict,
                cooldown_s: int = 3600) -> dict:
    err = validate_rule(kind, config)
    if err:
        raise ValueError(err)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT COUNT(*) FROM alert_rules WHERE user_id = ? AND active = 1"),
            (user_id,),
        )
        if int(cur.fetchone()[0]) >= MAX_RULES_PER_USER:
            raise ValueError(f"Rule limit reached ({MAX_RULES_PER_USER} per user).")
        now = time.time()
        rid = uuid.uuid4().hex[:12]
        cur.execute(
            _q("INSERT INTO alert_rules (id, user_id, kind, config, active, cooldown_s, created_at, updated_at) "
               "VALUES (?, ?, ?, ?, 1, ?, ?, ?)"),
            (rid, user_id, kind, json.dumps(config), int(cooldown_s), now, now),
        )
    return get_rule(user_id, rid)  # type: ignore[return-value]


def _row_to_rule(row) -> dict:
    return {
        "id": row[0],
        "user_id": row[1],
        "kind": row[2],
        "config": json.loads(row[3]),
        "active": bool(row[4]),
        "cooldown_s": int(row[5]),
        "last_fired_at": row[6],
        "created_at": row[7],
        "updated_at": row[8],
    }


def get_rule(user_id: str, rule_id: str) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT id, user_id, kind, config, active, cooldown_s, last_fired_at, created_at, updated_at "
               "FROM alert_rules WHERE id = ? AND user_id = ?"),
            (rule_id, user_id),
        )
        row = cur.fetchone()
    return _row_to_rule(row) if row else None


def list_rules(user_id: str) -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT id, user_id, kind, config, active, cooldown_s, last_fired_at, created_at, updated_at "
               "FROM alert_rules WHERE user_id = ? ORDER BY created_at DESC"),
            (user_id,),
        )
        return [_row_to_rule(r) for r in cur.fetchall()]


def update_rule(user_id: str, rule_id: str, *, active: bool | None = None,
                config: dict | None = None, cooldown_s: int | None = None) -> dict | None:
    rule = get_rule(user_id, rule_id)
    if rule is None:
        return None
    sets: list[str] = ["updated_at = ?"]
    args: list[object] = [time.time()]
    if active is not None:
        sets.append("active = ?")
        args.append(1 if active else 0)
    if config is not None:
        err = validate_rule(rule["kind"], config)
        if err:
            raise ValueError(err)
        sets.append("config = ?")
        args.append(json.dumps(config))
    if cooldown_s is not None:
        sets.append("cooldown_s = ?")
        args.append(int(cooldown_s))
    args.append(rule_id)
    with _conn() as conn:
        conn.cursor().execute(
            _q(f"UPDATE alert_rules SET {', '.join(sets)} WHERE id = ?"), args
        )
    return get_rule(user_id, rule_id)


def delete_rule(user_id: str, rule_id: str) -> bool:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("DELETE FROM alert_rules WHERE id = ? AND user_id = ?"),
            (rule_id, user_id),
        )
        return cur.rowcount > 0


def list_events(user_id: str, limit: int = 50) -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("SELECT id, rule_id, user_id, symbol, message, readings, channels, created_at "
               "FROM alert_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?"),
            (user_id, int(limit)),
        )
        return [
            {
                "id": r[0], "rule_id": r[1], "user_id": r[2], "symbol": r[3],
                "message": r[4],
                "readings": json.loads(r[5]) if r[5] else None,
                "channels": json.loads(r[6]) if r[6] else [],
                "created_at": r[7],
            }
            for r in cur.fetchall()
        ]


def list_active_rules_all_users() -> list[dict]:
    """Every active rule across all users — used by the scheduler check."""
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, kind, config, active, cooldown_s, last_fired_at, created_at, updated_at "
            "FROM alert_rules WHERE active = 1"
        )
        return [_row_to_rule(r) for r in cur.fetchall()]


def _record_event(rule: dict, symbol: str | None, message: str,
                  readings: dict, channels: list[str]) -> None:
    now = time.time()
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("INSERT INTO alert_events (id, rule_id, user_id, symbol, message, readings, channels, created_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"),
            (uuid.uuid4().hex[:12], rule["id"], rule["user_id"], symbol,
             message, json.dumps(readings, default=str), json.dumps(channels), now),
        )
        cur.execute(
            _q("UPDATE alert_rules SET last_fired_at = ?, updated_at = ? WHERE id = ?"),
            (now, now, rule["id"]),
        )


# --- notification channels ----------------------------------------------------

def _notify_telegram(chat_id: str, text: str) -> bool:
    import os
    import urllib.request

    token = os.environ.get("ALERT_TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not chat_id:
        return False
    try:
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        logger.exception("telegram notify failed")
        return False


def _notify_email(to_addr: str, subject: str, body: str) -> bool:
    import os
    import smtplib
    from email.message import EmailMessage
    from urllib.parse import urlparse

    smtp_url = os.environ.get("ALERT_SMTP_URL", "").strip()
    from_addr = os.environ.get("ALERT_EMAIL_FROM", "").strip()
    if not smtp_url or not from_addr or not to_addr:
        return False
    try:
        u = urlparse(smtp_url)
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = from_addr, to_addr, subject
        msg.set_content(body)
        port = u.port or 587
        with smtplib.SMTP(u.hostname or "", port, timeout=15) as s:
            s.starttls()
            if u.username:
                s.login(u.username, u.password or "")
            s.send_message(msg)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("email notify failed")
        return False


def _deliver(rule: dict, message: str) -> list[str]:
    """Attempt every channel the rule is configured for. Returns channels used."""
    import os

    cfg = rule["config"]
    used: list[str] = []
    chat_id = (cfg.get("telegram_chat_id")
               or os.environ.get("ALERT_TELEGRAM_DEFAULT_CHAT_ID", "")).strip()
    if chat_id and _notify_telegram(chat_id, message):
        used.append("telegram")
    email = (cfg.get("email") or "").strip()
    if email and _notify_email(email, "Trading Copilot alert", message):
        used.append("email")
    return used


# --- evaluation ----------------------------------------------------------------

def evaluate_rules(*, trigger_test_rule_id: str | None = None) -> dict:
    """Evaluate all active rules once. Fires those whose condition is met and
    whose cooldown has elapsed. Returns a summary dict.

    `trigger_test_rule_id` forces evaluation of one rule ignoring cooldown —
    used by the UI's "send test alert" button (still requires the condition to
    be checked honestly; for price rules a test send reports the current price).
    """
    import os

    from backend.data.providers import get_provider
    from backend.signals.scanner import scan_watchlist

    rules = list_active_rules_all_users()
    fired, checked, errors = 0, 0, 0

    # Group scanner rules so we run ONE scan per unique symbol set, not one
    # scan per rule.
    scanner_cache: dict[tuple, dict] = {}

    for rule in rules:
        checked += 1
        cfg = rule["config"]
        now = time.time()
        on_cooldown = (
            rule["last_fired_at"] is not None
            and now - float(rule["last_fired_at"]) < rule["cooldown_s"]
        )
        force = trigger_test_rule_id == rule["id"]
        if on_cooldown and not force:
            continue
        try:
            if rule["kind"] in PRICE_KINDS:
                symbol = cfg["symbol"].strip().upper()
                df = get_provider(symbol).fetch_klines(symbol, "1h", 2)
                price = float(df["close"].iloc[-1])
                target = float(cfg["value"])
                hit = (rule["kind"] == "price_above" and price >= target) or \
                      (rule["kind"] == "price_below" and price <= target)
                readings = {"price": price, "threshold": target, "kind": rule["kind"]}
                if hit or force:
                    direction = "above" if rule["kind"] == "price_above" else "below"
                    status = "" if hit else " (TEST — condition not currently met)"
                    msg = (f"[{symbol}] price alert{status}: last {price:,.6g} is "
                           f"{'now ' if hit else 'not yet '}{direction} your {target:,.6g} threshold.")
                    channels = _deliver(rule, msg)
                    _record_event(rule, symbol, msg, readings, channels)
                    if hit:
                        fired += 1
            elif rule["kind"] in SCANNER_KINDS:
                symbols = tuple(sorted(s.strip().upper() for s in cfg["symbols"]))
                interval = cfg.get("interval", "1h")
                key = (symbols, interval)
                if key not in scanner_cache:
                    scanner_cache[key] = {
                        r["symbol"]: r for r in scan_watchlist(list(symbols), interval)
                    }
                results = scanner_cache[key]
                want_lean = cfg["lean"]
                hits = [r for r in results.values() if r.get("lean") == want_lean]
                readings = {"lean_wanted": want_lean,
                            "hits": {s: r.get("score") for s, r in ((h.get("symbol"), h) for h in hits)}}
                if hits or force:
                    if hits:
                        names = ", ".join(sorted(r["symbol"] for r in hits))
                        msg = f"Scanner alert: {want_lean} lean detected on {names} ({interval})."
                    else:
                        msg = (f"Scanner alert (TEST — no {want_lean} symbols right now) "
                               f"over {len(symbols)} symbols ({interval}).")
                    channels = _deliver(rule, msg)
                    _record_event(rule, None, msg, readings, channels)
                    if hits:
                        fired += 1
        except Exception:  # noqa: BLE001 — one bad rule must not kill the sweep
            errors += 1
            logger.exception("alert rule %s evaluation failed", rule["id"])

    return {"checked": checked, "fired": fired, "errors": errors,
            "dry_run": not bool(os.environ.get("ALERT_DELIVERY_ENABLED", "1") == "1")}
