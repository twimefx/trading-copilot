"""AI Trade Journal — behavioral coaching (Phase 2 retention feature).

Turns a user's own trade history into an honest behavioral mirror. Two layers:

  1. A DETERMINISTIC pattern detector reads the closed-trade record and flags
     concrete, evidence-backed behavioral tendencies (overtrading, revenge
     trading, cutting winners / letting losers run, conviction miscalibration,
     symbol concentration, ignored stops). Every flag carries the numbers that
     triggered it — no black box, no vibes.

  2. An LLM COACH is then asked to write short, direct, personalized coaching
     GROUNDED ONLY on those detected patterns + the aggregate stats. The model
     never sees raw trades and is told not to invent patterns — it explains and
     prioritizes what the detector already found. This keeps the feature honest
     and cheap: it runs on the router's cheaper SIGNAL_SUMMARY tier, not Opus.

Design rationale (Phase 0 finding + Tim's honesty bar): the moat is coaching /
explainability, and we never fabricate insight. If there isn't enough data, we
say so plainly rather than hallucinating a personality profile.
"""
from __future__ import annotations

import json
from collections import Counter

from backend.ai.router import AIRouter, TaskClass

# Minimum decided (win/loss) trades before we attempt behavioral coaching.
# Below this the sample is noise and any "pattern" is overfitting.
MIN_TRADES_FOR_COACHING = 5

COACH_DISCLAIMER = (
    "This is behavioral coaching based on your own logged trades, not financial "
    "advice. It reflects patterns in your record, not a prediction of future results."
)

COACH_SYSTEM_PROMPT = """You are a disciplined, supportive trading-performance coach.

You are given (a) aggregate stats from a trader's OWN closed-trade journal and
(b) a list of behavioral patterns already DETECTED deterministically from their
record, each with the numbers that triggered it.

RULES:
- Coach ONLY on the detected patterns and stats provided. Do NOT invent patterns,
  numbers, or trades that are not in the input.
- Be direct and specific. Reference the actual numbers (win rate, avg win vs avg
  loss, streaks) so the trader sees the evidence.
- Prioritize: lead with the pattern most damaging to their edge.
- Be constructive — pair each critique with one concrete, actionable adjustment.
- Never guarantee results or give financial advice. This is process coaching.
- No self-correction or hedging mid-sentence. State finished, plain claims.

Respond ONLY with valid JSON in exactly this shape:
{
  "headline": "<one-sentence honest read of their trading behavior>",
  "focus_areas": [
    {"pattern": "<short label>", "insight": "<what the data shows>", "action": "<one concrete fix>"}
  ],
  "encouragement": "<one honest, non-flattering line on what they're doing right, or how to build discipline>"
}"""


def _safe_num(x) -> float | None:
    try:
        if x is None:
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def detect_patterns(closed_trades: list[dict]) -> dict:
    """Deterministically derive behavioral stats + flags from closed trades.

    `closed_trades` are journal rows (status == 'closed'), newest-first as stored.
    Returns a structured, evidence-backed profile the LLM coach reasons over.
    Pure/testable — no LLM, no network.
    """
    # Order oldest -> newest so streak logic reads chronologically.
    trades = list(reversed(closed_trades))

    outcomes = [t.get("outcome") for t in trades]
    wins = sum(1 for o in outcomes if o == "win")
    losses = sum(1 for o in outcomes if o == "loss")
    decided = wins + losses

    win_pnls = [p for t in trades if t.get("outcome") == "win"
                and (p := _safe_num(t.get("pnl"))) is not None and p > 0]
    loss_pnls = [-p for t in trades if t.get("outcome") == "loss"
                 and (p := _safe_num(t.get("pnl"))) is not None and p < 0]
    avg_win = round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else None
    avg_loss = round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else None
    total_pnl = round(sum(v for t in trades if (v := _safe_num(t.get("pnl"))) is not None), 2)

    # Longest losing streak (chronological).
    longest_loss_streak = cur = 0
    for o in outcomes:
        if o == "loss":
            cur += 1
            longest_loss_streak = max(longest_loss_streak, cur)
        else:
            cur = 0

    # Symbol concentration.
    sym_counts = Counter(t.get("symbol") for t in trades if t.get("symbol"))
    top_symbol, top_symbol_n = (sym_counts.most_common(1)[0] if sym_counts else (None, 0))

    # Conviction calibration: did higher-conviction ideas actually win more?
    hi_conv = [t for t in trades if (c := t.get("conviction")) is not None and c >= 70]
    hi_conv_decided = [t for t in hi_conv if t.get("outcome") in ("win", "loss")]
    hi_conv_win_rate = (
        round(sum(1 for t in hi_conv_decided if t.get("outcome") == "win")
              / len(hi_conv_decided), 3)
        if hi_conv_decided else None
    )

    win_rate = round(wins / decided, 3) if decided else None
    # Reward:risk realized (avg win size vs avg loss size).
    payoff_ratio = round(avg_win / avg_loss, 2) if (avg_win and avg_loss) else None

    stats = {
        "closed_trades": len(trades),
        "decided_trades": decided,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": payoff_ratio,
        "total_pnl": total_pnl,
        "longest_loss_streak": longest_loss_streak,
        "top_symbol": top_symbol,
        "top_symbol_share": (round(top_symbol_n / len(trades), 3) if trades else None),
        "high_conviction_win_rate": hi_conv_win_rate,
    }

    flags: list[dict] = []

    # Cutting winners / letting losers run: losers bigger than winners.
    if payoff_ratio is not None and payoff_ratio < 1.0:
        flags.append({
            "pattern": "losers_bigger_than_winners",
            "detail": (f"Average loss ${avg_loss} exceeds average win ${avg_win} "
                       f"(payoff ratio {payoff_ratio}). Losses are being let run "
                       f"and/or winners cut short."),
        })

    # Win-rate can't save a bad payoff, or vice-versa: net-negative despite decent win rate.
    if win_rate is not None and win_rate >= 0.5 and total_pnl < 0:
        flags.append({
            "pattern": "profitable_hit_rate_unprofitable_pnl",
            "detail": (f"Win rate is {int(win_rate*100)}% but total P&L is "
                       f"${total_pnl}. The math is broken by trade sizing / "
                       f"letting losses run, not by being wrong."),
        })

    # Revenge / tilt risk: a long losing streak.
    if longest_loss_streak >= 4:
        flags.append({
            "pattern": "extended_losing_streak",
            "detail": (f"Longest losing streak is {longest_loss_streak} trades in a row "
                       f"— high risk of revenge trading and position-sizing up on tilt."),
        })

    # Overconcentration in one symbol.
    share = stats["top_symbol_share"]
    if top_symbol and share is not None and share >= 0.6 and len(trades) >= 5:
        flags.append({
            "pattern": "symbol_concentration",
            "detail": (f"{int(share*100)}% of trades are in {top_symbol}. "
                       f"Edge and risk are concentrated in a single market."),
        })

    # Conviction miscalibration: high-conviction ideas don't outperform.
    if (hi_conv_win_rate is not None and win_rate is not None
            and len(hi_conv_decided) >= 3 and hi_conv_win_rate <= win_rate):
        flags.append({
            "pattern": "conviction_miscalibrated",
            "detail": (f"High-conviction (>=70) ideas win {int(hi_conv_win_rate*100)}% "
                       f"vs {int(win_rate*100)}% overall — conviction is not tracking "
                       f"real edge and may be emotion, not signal."),
        })

    return {"stats": stats, "flags": flags}


def _rule_based_headline(profile: dict) -> str:
    """Deterministic fallback headline when the LLM path is unavailable."""
    s = profile["stats"]
    if profile["flags"]:
        return (f"{s['decided_trades']} decided trades, {int((s['win_rate'] or 0)*100)}% win rate, "
                f"${s['total_pnl']} net — {len(profile['flags'])} behavioral pattern(s) to address.")
    return (f"{s['decided_trades']} decided trades, {int((s['win_rate'] or 0)*100)}% win rate, "
            f"${s['total_pnl']} net — no strong negative patterns detected. Keep logging.")


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    return t


def coach(closed_trades: list[dict], router: AIRouter | None = None) -> dict:
    """Produce behavioral coaching for a trader's closed-trade history.

    Returns a structured dict:
      { enough_data: bool, stats: {...}, patterns: [...], coaching: {...} | None,
        disclaimer: str, cost_usd: float }

    If there aren't enough decided trades, returns enough_data=False with the raw
    stats and NO fabricated coaching — honesty over a fake profile.
    """
    profile = detect_patterns(closed_trades)
    stats = profile["stats"]
    flags = profile["flags"]

    if stats["decided_trades"] < MIN_TRADES_FOR_COACHING:
        return {
            "enough_data": False,
            "min_trades": MIN_TRADES_FOR_COACHING,
            "stats": stats,
            "patterns": flags,
            "coaching": None,
            "message": (
                f"Log at least {MIN_TRADES_FOR_COACHING} decided (win/loss) trades to "
                f"unlock behavioral coaching. You have {stats['decided_trades']}."
            ),
            "disclaimer": COACH_DISCLAIMER,
            "cost_usd": 0.0,
        }

    router = router or AIRouter()
    prompt = (
        "Here is the trader's closed-journal profile.\n\n"
        f"AGGREGATE STATS:\n{json.dumps(stats, indent=2)}\n\n"
        f"DETECTED BEHAVIORAL PATTERNS (evidence-backed, do not invent others):\n"
        f"{json.dumps(flags, indent=2)}\n\n"
        "Write grounded, prioritized coaching per your rules. Respond with JSON only."
    )
    # Coaching is high-value but not Opus-heavy -> route to the cheaper tier.
    raw = router.complete(TaskClass.SIGNAL_SUMMARY, prompt,
                          system=COACH_SYSTEM_PROMPT, max_tokens=900)

    try:
        coaching = json.loads(_strip_fences(raw))
    except json.JSONDecodeError:
        # Never fabricate; degrade to a deterministic summary the user can trust.
        coaching = {
            "headline": _rule_based_headline(profile),
            "focus_areas": [
                {"pattern": f["pattern"], "insight": f["detail"], "action": ""}
                for f in flags
            ],
            "encouragement": "",
            "generated": "rule-based-fallback",
        }

    return {
        "enough_data": True,
        "stats": stats,
        "patterns": flags,
        "coaching": coaching,
        "disclaimer": COACH_DISCLAIMER,
        "cost_usd": round(router.cost_log.total_usd, 5),
    }
