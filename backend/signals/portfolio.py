"""Portfolio Copilot — portfolio-level intelligence over a trader's OPEN positions.

Phase 2 retention feature, same honesty architecture as the journal coach:

  1. DETERMINISTIC risk core reads the user's open journal entries, marks each to
     a live price, and computes real portfolio risk — notional exposure,
     direction-aware unrealized P&L, gross/net exposure, net directional bias,
     and concentration (largest name, per-asset-class share). It then flags
     concrete risks with the numbers behind them (single-name overexposure,
     one-directional book, crypto-only concentration, positions underwater past
     their stop, an oversized position relative to the rest).

  2. An LLM reads ONLY that computed risk profile and writes a prioritized,
     plain-English portfolio read (biggest risks + concrete rebalancing/hedging
     suggestions). It never invents positions or risks — it explains the math.
     Runs on the router's cheaper SIGNAL_SUMMARY tier, not Opus.

If there are no open positions we say so plainly and make NO LLM call.
Positions missing entry_price/size are still counted for concentration but are
excluded from P&L math and marked "incomplete" — we never fabricate a fill.
"""
from __future__ import annotations

import json

from backend.ai.router import AIRouter, TaskClass
from backend.data.providers import asset_class, get_provider
from backend.data.indicators import snapshot

PORTFOLIO_DISCLAIMER = (
    "This is an AI risk read of your own logged open positions, not financial "
    "advice. It reflects your current book, not a prediction. Trading involves "
    "substantial risk of loss."
)

PORTFOLIO_SYSTEM_PROMPT = """You are a disciplined portfolio risk manager for a retail trader.

You are given a DETERMINISTIC risk profile computed from the trader's own open
positions: per-position marks and unrealized P&L, gross/net exposure, net
directional bias, concentration metrics, and a list of already-DETECTED risk
flags (each with the numbers that triggered it).

RULES:
- Assess ONLY the positions, metrics, and flags provided. Do NOT invent positions,
  numbers, or risks that are not in the input.
- Be direct and specific — cite the actual numbers (exposure, concentration %,
  net bias, unrealized P&L) so the trader sees the evidence.
- Prioritize: lead with the risk most likely to hurt the book.
- Pair each risk with one concrete, actionable adjustment (trim, hedge, add a stop,
  diversify) — process guidance, not a directional prediction.
- Never guarantee outcomes or give financial advice.
- No self-correction or hedging mid-sentence. State finished, plain claims.

Respond ONLY with valid JSON in exactly this shape:
{
  "headline": "<one-sentence honest read of the book's risk posture>",
  "risks": [
    {"risk": "<short label>", "detail": "<what the numbers show>", "action": "<one concrete adjustment>"}
  ],
  "suggestions": ["<optional extra portfolio-level suggestion>", ...]
}"""


def _num(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def live_price(symbol: str, interval: str = "1h") -> float | None:
    """Best-effort latest close for a symbol via its data provider. None on failure."""
    try:
        provider = get_provider(symbol)
        df = provider.fetch_klines(symbol, interval, 50)
        snap = snapshot(df)
        return _num(snap.get("last_close"))
    except Exception:  # noqa: BLE001 — a dead symbol must not break the whole book
        return None


def _price_lookup(symbols: list[str], interval: str = "1h") -> dict[str, float | None]:
    """Fetch live prices for a set of symbols (deduped)."""
    return {s: live_price(s, interval) for s in dict.fromkeys(symbols)}


def mark_positions(open_entries: list[dict],
                   prices: dict[str, float | None]) -> list[dict]:
    """Mark each open position to a live price. Pure/testable (prices injected).

    Per-position output: symbol, asset_class, direction, size, entry_price, mark,
    notional (abs), unrealized P&L (direction-aware), pct_move, and `complete`
    (False when entry/size are missing so it's excluded from P&L math).
    """
    marked: list[dict] = []
    for e in open_entries:
        symbol = (e.get("symbol") or "").upper()
        direction = (e.get("direction") or "none").lower()
        entry = _num(e.get("entry_price"))
        size = _num(e.get("size"))
        mark = prices.get(symbol)
        complete = entry is not None and size is not None and size > 0 and direction in ("long", "short")

        # Mark for notional; fall back to entry when we have no live price.
        ref_price = mark if mark is not None else entry
        notional = None
        if size is not None and ref_price is not None:
            notional = round(abs(size * ref_price), 2)

        upnl = None
        pct_move = None
        if complete and mark is not None and entry is not None and size is not None:
            sign = 1.0 if direction == "long" else -1.0
            upnl = round(sign * (mark - entry) * size, 2)
            if entry:
                pct_move = round(sign * (mark - entry) / entry * 100, 2)

        marked.append({
            "id": e.get("id"),
            "symbol": symbol,
            "asset_class": asset_class(symbol) if symbol else "unknown",
            "direction": direction,
            "size": size,
            "entry_price": entry,
            "mark": mark,
            "stop_price": _num(e.get("stop_price")),
            "notional": notional,
            "unrealized_pnl": upnl,
            "pct_move": pct_move,
            "complete": complete,
        })
    return marked


def assess(open_entries: list[dict],
           prices: dict[str, float | None] | None = None,
           interval: str = "1h") -> dict:
    """Deterministic portfolio risk profile + flags. No LLM, no network if prices given."""
    if prices is None:
        prices = _price_lookup([(e.get("symbol") or "").upper() for e in open_entries], interval)

    positions = mark_positions(open_entries, prices)
    priced = [p for p in positions if p["notional"] is not None]

    gross = round(sum(p["notional"] for p in priced), 2)
    long_notional = round(sum(p["notional"] for p in priced if p["direction"] == "long"), 2)
    short_notional = round(sum(p["notional"] for p in priced if p["direction"] == "short"), 2)
    net = round(long_notional - short_notional, 2)
    total_upnl = round(sum(p["unrealized_pnl"] for p in positions
                           if p["unrealized_pnl"] is not None), 2)

    # Concentration.
    largest = max(priced, key=lambda p: p["notional"], default=None)
    largest_share = round(largest["notional"] / gross, 3) if (largest and gross) else None

    class_notional: dict[str, float] = {}
    for p in priced:
        class_notional[p["asset_class"]] = class_notional.get(p["asset_class"], 0.0) + p["notional"]
    class_share = {k: round(v / gross, 3) for k, v in class_notional.items()} if gross else {}

    # Net directional bias (as a share of gross).
    net_bias_pct = round(net / gross, 3) if gross else None
    if net_bias_pct is None:
        bias = "unknown"
    elif net_bias_pct >= 0.6:
        bias = "net_long"
    elif net_bias_pct <= -0.6:
        bias = "net_short"
    else:
        bias = "balanced"

    profile = {
        "open_positions": len(positions),
        "priced_positions": len(priced),
        "incomplete_positions": sum(1 for p in positions if not p["complete"]),
        "gross_exposure": gross,
        "long_exposure": long_notional,
        "short_exposure": short_notional,
        "net_exposure": net,
        "net_bias": bias,
        "net_bias_pct": net_bias_pct,
        "total_unrealized_pnl": total_upnl,
        "largest_position": (largest["symbol"] if largest else None),
        "largest_position_share": largest_share,
        "asset_class_share": class_share,
        "positions": positions,
    }

    flags: list[dict] = []

    # Single-name overexposure.
    if largest and largest_share is not None and largest_share >= 0.4 and len(priced) >= 2:
        flags.append({
            "risk": "single_name_concentration",
            "detail": (f"{largest['symbol']} is {int(largest_share*100)}% of gross exposure "
                       f"(${largest['notional']} of ${gross}). One position dominates the book."),
        })

    # One-directional book.
    if bias in ("net_long", "net_short") and net_bias_pct is not None and len(priced) >= 2:
        flags.append({
            "risk": "one_directional_book",
            "detail": (f"Book is {bias.replace('_', ' ')} ({int(abs(net_bias_pct)*100)}% of gross "
                       f"one way). Little internal hedge if the market turns."),
        })

    # Asset-class concentration (e.g. all crypto).
    for cls, share in class_share.items():
        if share >= 0.8 and len(priced) >= 2:
            flags.append({
                "risk": "asset_class_concentration",
                "detail": (f"{int(share*100)}% of exposure is in {cls}. Correlated names move "
                           f"together — diversification across asset classes is thin."),
            })

    # Positions underwater past their stop (stop breached but still open in journal).
    for p in positions:
        if p["complete"] and p["mark"] is not None and p["stop_price"] is not None:
            breached = (p["direction"] == "long" and p["mark"] < p["stop_price"]) or \
                       (p["direction"] == "short" and p["mark"] > p["stop_price"])
            if breached:
                flags.append({
                    "risk": "stop_breached_still_open",
                    "detail": (f"{p['symbol']} ({p['direction']}) is past its stop "
                               f"({p['mark']} vs stop {p['stop_price']}) but still marked open. "
                               f"A stop you don't honor isn't a stop."),
                })

    # Oversized single position relative to the average of the rest.
    if len(priced) >= 3 and largest is not None:
        others = [p["notional"] for p in priced if p is not largest]
        avg_other = sum(others) / len(others) if others else 0.0
        if avg_other > 0 and largest["notional"] >= 3 * avg_other:
            flags.append({
                "risk": "oversized_position",
                "detail": (f"{largest['symbol']} (${largest['notional']}) is over 3x the average "
                           f"of your other positions (${round(avg_other, 2)}). Sizing is uneven."),
            })

    return {"profile": profile, "flags": flags}


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    return t


def _rule_based_read(assessment: dict) -> dict:
    prof = assessment["profile"]
    flags = assessment["flags"]
    headline = (f"{prof['priced_positions']} priced position(s), ${prof['gross_exposure']} gross, "
                f"{prof['net_bias'].replace('_', ' ')} bias, "
                f"${prof['total_unrealized_pnl']} unrealized — "
                f"{len(flags)} risk flag(s).")
    return {
        "headline": headline,
        "risks": [{"risk": f["risk"], "detail": f["detail"], "action": ""} for f in flags],
        "suggestions": [],
        "generated": "rule-based-fallback",
    }


def portfolio_copilot(open_entries: list[dict],
                      prices: dict[str, float | None] | None = None,
                      router: AIRouter | None = None,
                      interval: str = "1h") -> dict:
    """Full Portfolio Copilot: deterministic risk profile + grounded LLM read.

    Returns { has_positions, profile, flags, read | None, disclaimer, cost_usd }.
    No open positions -> has_positions False, no LLM call, no fabricated read.
    """
    if not open_entries:
        return {
            "has_positions": False,
            "profile": {"open_positions": 0},
            "flags": [],
            "read": None,
            "message": "No open positions logged. Mark journal entries as 'open' to track your book.",
            "disclaimer": PORTFOLIO_DISCLAIMER,
            "cost_usd": 0.0,
        }

    assessment = assess(open_entries, prices=prices, interval=interval)
    profile = assessment["profile"]
    flags = assessment["flags"]

    router = router or AIRouter()
    # Keep the prompt lean: send metrics + flags, not the full positions blob twice.
    prompt_profile = {k: v for k, v in profile.items() if k != "positions"}
    prompt_profile["positions"] = [
        {k: p[k] for k in ("symbol", "asset_class", "direction", "notional",
                           "unrealized_pnl", "pct_move", "complete")}
        for p in profile["positions"]
    ]
    prompt = (
        "Here is the trader's open-book risk profile.\n\n"
        f"RISK PROFILE:\n{json.dumps(prompt_profile, indent=2)}\n\n"
        f"DETECTED RISK FLAGS (evidence-backed, do not invent others):\n"
        f"{json.dumps(flags, indent=2)}\n\n"
        "Write a grounded, prioritized portfolio risk read per your rules. Respond with JSON only."
    )
    raw = router.complete(TaskClass.SIGNAL_SUMMARY, prompt,
                          system=PORTFOLIO_SYSTEM_PROMPT, max_tokens=900)

    try:
        read = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        read = _rule_based_read(assessment)

    return {
        "has_positions": True,
        "profile": profile,
        "flags": flags,
        "read": read,
        "disclaimer": PORTFOLIO_DISCLAIMER,
        "cost_usd": round(router.cost_log.total_usd, 5),
    }
