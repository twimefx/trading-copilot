"""Subscription tiers — the single source of truth for pricing, quotas, and gates.

Three tiers (from the master plan):
  free     — delayed/limited: a few Copilot calls/day, scanner limited.
  pro      — live signals, scanner, journal, higher Copilot quota.
  premium  — everything, highest quota.

Everything here is env-overridable so we can tune prices/quotas in prod WITHOUT a
redeploy. Stripe price IDs map a paid Stripe subscription back to a tier in the
webhook. Quotas are per-UTC-day Copilot analysis counts (the only endpoint that
costs real LLM money); -1 means unlimited.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

FREE = "free"
PRO = "pro"
PREMIUM = "premium"
VALID_TIERS = {FREE, PRO, PREMIUM}


@dataclass(frozen=True)
class Tier:
    name: str
    # Monthly USD price (0 for free). Display only — Stripe is the billing authority.
    price_usd: int
    # Per-UTC-day Copilot analysis quota. -1 = unlimited.
    daily_copilot_quota: int
    # Max symbols the scanner will screen in one call.
    scan_max_symbols: int
    # Feature flags gated by tier.
    features: frozenset[str]


def _int(env: str, default: int) -> int:
    try:
        return int(os.environ.get(env, str(default)))
    except ValueError:
        return default


# Feature keys used by endpoint gates.
F_COPILOT = "copilot"
F_SCANNER = "scanner"
F_JOURNAL = "journal"
F_FOREX = "forex"          # forex/metals symbols (Oanda) — a paid perk
F_DEBATE = "debate"        # multi-agent debate engine — Premium flagship

TIERS: dict[str, Tier] = {
    FREE: Tier(
        name=FREE,
        price_usd=0,
        daily_copilot_quota=_int("FREE_DAILY_COPILOT_QUOTA", 3),
        scan_max_symbols=_int("FREE_SCAN_MAX_SYMBOLS", 5),
        features=frozenset({F_COPILOT, F_SCANNER}),
    ),
    PRO: Tier(
        name=PRO,
        price_usd=_int("PRO_PRICE_USD", 49),
        daily_copilot_quota=_int("PRO_DAILY_COPILOT_QUOTA", 100),
        scan_max_symbols=_int("PRO_SCAN_MAX_SYMBOLS", 25),
        features=frozenset({F_COPILOT, F_SCANNER, F_JOURNAL, F_FOREX}),
    ),
    PREMIUM: Tier(
        name=PREMIUM,
        price_usd=_int("PREMIUM_PRICE_USD", 199),
        daily_copilot_quota=_int("PREMIUM_DAILY_COPILOT_QUOTA", -1),
        scan_max_symbols=_int("PREMIUM_SCAN_MAX_SYMBOLS", 100),
        features=frozenset({F_COPILOT, F_SCANNER, F_JOURNAL, F_FOREX, F_DEBATE}),
    ),
}


def get_tier(name: str | None) -> Tier:
    """Resolve a tier by name, defaulting to free for unknown/None."""
    return TIERS.get((name or FREE).lower(), TIERS[FREE])


def has_feature(tier_name: str | None, feature: str) -> bool:
    return feature in get_tier(tier_name).features


# --- Stripe price ID -> tier mapping (set these once Stripe products exist) ---
# The webhook reads the subscription's price id and maps it back to our tier.
def price_to_tier() -> dict[str, str]:
    mapping: dict[str, str] = {}
    pro_price = os.environ.get("STRIPE_PRICE_PRO", "").strip()
    prem_price = os.environ.get("STRIPE_PRICE_PREMIUM", "").strip()
    if pro_price:
        mapping[pro_price] = PRO
    if prem_price:
        mapping[prem_price] = PREMIUM
    return mapping


def tier_to_price(tier_name: str) -> str | None:
    """Reverse: tier -> Stripe price id (for creating a Checkout session)."""
    if tier_name == PRO:
        return os.environ.get("STRIPE_PRICE_PRO", "").strip() or None
    if tier_name == PREMIUM:
        return os.environ.get("STRIPE_PRICE_PREMIUM", "").strip() or None
    return None
