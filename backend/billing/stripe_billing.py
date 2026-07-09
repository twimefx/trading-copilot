"""Stripe billing — Checkout session creation + webhook subscription sync.

Flow:
  1. Signed-in user clicks "Upgrade to Pro" -> frontend POSTs /billing/checkout
     {tier} with their Clerk JWT. We create (or reuse) a Stripe customer keyed to
     their user id, open a Checkout Session for that tier's price, return the URL.
  2. User pays on Stripe's hosted page -> Stripe redirects back to the frontend.
  3. Stripe fires webhooks to /billing/webhook. We verify the signature and sync
     the user's tier in our DB:
       checkout.session.completed          -> link customer, set tier from price
       customer.subscription.updated       -> set tier from current price (up/downgrade)
       customer.subscription.deleted       -> downgrade to free (cancellation)

Stripe is the billing AUTHORITY; our `users.tier` is a cache the webhook keeps
in sync. Never trust a client-sent tier.

Config (env):
  STRIPE_SECRET_KEY        sk_live_*** / sk_test_***
  STRIPE_WEBHOOK_SECRET    whsec_*** (from the webhook endpoint in Stripe dashboard)
  STRIPE_PRICE_PRO         price_*** for the Pro subscription
  STRIPE_PRICE_PREMIUM     price_*** for the Premium subscription
  BILLING_SUCCESS_URL      where Stripe returns after success
  BILLING_CANCEL_URL       where Stripe returns on cancel
"""
from __future__ import annotations

import os

from backend.billing import FREE, price_to_tier, tier_to_price
from backend.billing import users as user_store


def _secret_key() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY not configured")
    return key


def _client():
    import stripe
    stripe.api_key = _secret_key()
    return stripe


def create_checkout_session(user_id: str, tier: str) -> str:
    """Create a Stripe Checkout Session for `tier` and return its hosted URL."""
    price_id = tier_to_price(tier)
    if not price_id:
        raise ValueError(f"no Stripe price configured for tier '{tier}'")

    stripe = _client()
    user = user_store.get_or_create_user(user_id)

    # Reuse an existing Stripe customer for this user, else create one keyed to
    # the Clerk user id (so the webhook can map back even before checkout completes).
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(metadata={"user_id": user_id})
        customer_id = customer["id"]
        user_store.link_stripe_customer(user_id, customer_id)

    success_url = os.environ.get(
        "BILLING_SUCCESS_URL", "https://twimetrade.app/?upgraded=1"
    )
    cancel_url = os.environ.get(
        "BILLING_CANCEL_URL", "https://twimetrade.app/?upgrade=cancelled"
    )
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=user_id,
        metadata={"user_id": user_id, "tier": tier},
        allow_promotion_codes=True,
    )
    return session["url"]


def create_billing_portal_session(user_id: str) -> str:
    """Stripe Billing Portal — lets a paying user manage/cancel their subscription."""
    stripe = _client()
    user = user_store.get_or_create_user(user_id)
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise ValueError("no Stripe customer for this user")
    return_url = os.environ.get(
        "BILLING_PORTAL_RETURN_URL", "https://twimetrade.app/"
    )
    session = stripe.billing_portal.Session.create(
        customer=customer_id, return_url=return_url
    )
    return session["url"]


def _tier_from_subscription(subscription: dict) -> str:
    """Map a Stripe subscription's active price id back to our tier."""
    mapping = price_to_tier()
    items = (subscription.get("items") or {}).get("data") or []
    for item in items:
        price = (item.get("price") or {})
        price_id = price.get("id")
        if price_id and price_id in mapping:
            return mapping[price_id]
    return FREE


def verify_and_parse_event(payload: bytes, sig_header: str) -> dict:
    """Verify the webhook signature and return the Stripe event object.

    Raises ValueError on bad signature / missing secret (caller -> 400).
    """
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise ValueError("STRIPE_WEBHOOK_SECRET not configured")
    stripe = _client()
    try:
        return stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as e:  # signature/parse failure
        raise ValueError(f"invalid webhook signature: {e}") from e


def handle_event(event: dict) -> dict:
    """Apply a verified Stripe event to our user tier state. Returns a summary."""
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if etype == "checkout.session.completed":
        user_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id")
        customer_id = obj.get("customer")
        tier = (obj.get("metadata") or {}).get("tier")
        sub_id = obj.get("subscription")
        if user_id and tier:
            user_store.set_tier(user_id, tier,
                                 stripe_customer_id=customer_id,
                                 stripe_subscription_id=sub_id)
            return {"action": "set_tier", "user_id": user_id, "tier": tier}
        return {"action": "ignored", "reason": "missing user_id/tier"}

    if etype in ("customer.subscription.updated", "customer.subscription.created"):
        customer_id = obj.get("customer")
        user = user_store.find_by_stripe_customer(customer_id)
        if not user:
            return {"action": "ignored", "reason": "unknown customer"}
        # A canceled/incomplete sub should not grant a paid tier.
        status = obj.get("status")
        if status in ("canceled", "incomplete_expired", "unpaid"):
            tier = FREE
        else:
            tier = _tier_from_subscription(obj)
        user_store.set_tier(user["user_id"], tier,
                            stripe_subscription_id=obj.get("id"))
        return {"action": "set_tier", "user_id": user["user_id"], "tier": tier}

    if etype == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        user = user_store.find_by_stripe_customer(customer_id)
        if not user:
            return {"action": "ignored", "reason": "unknown customer"}
        user_store.set_tier(user["user_id"], FREE)
        return {"action": "set_tier", "user_id": user["user_id"], "tier": FREE}

    return {"action": "ignored", "reason": f"unhandled event type {etype}"}
