"""Clerk authentication — verify the session JWT and derive the user id.

Clerk issues a short-lived RS256 JWT (the "session token") to the browser after
sign-in. The frontend sends it as `Authorization: Bearer <token>`. We verify the
signature against Clerk's published JWKS (rotated public keys, cached), check the
standard claims, and return the Clerk user id (`sub`).

That user id becomes the `owner_id` the rest of the app already scopes data by —
so auth slots into the existing seam with no data-model churn.

Config (env):
  CLERK_JWKS_URL      Clerk Frontend API JWKS endpoint
                      (e.g. https://<slug>.clerk.accounts.dev/.well-known/jwks.json).
  CLERK_ISSUER        Expected `iss` claim (e.g. https://<slug>.clerk.accounts.dev).
                      Optional but recommended; when set, tokens from other issuers
                      are rejected.
  CLERK_AUTHORIZED_PARTIES  Optional comma-separated list of allowed `azp` origins
                      (your frontend URLs). When set, tokens minted for other
                      origins are rejected (defense against token replay).

Dev/testing:
  If AUTH_DEV_ALLOW_HEADER=1 (default OFF), an `X-Owner-Id` header is accepted as a
  fallback identity when no bearer token is present. This preserves the old
  anonymous flow for local dev and keeps the existing test-suite green without a
  live Clerk instance. It is OFF in production.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

import jwt  # PyJWT
import requests
from fastapi import Header, HTTPException, Request
from jwt import PyJWKClient

CLERK_JWKS_URL = os.environ.get("CLERK_JWKS_URL", "").strip()
CLERK_ISSUER = os.environ.get("CLERK_ISSUER", "").strip()
_AZP_RAW = os.environ.get("CLERK_AUTHORIZED_PARTIES", "").strip()
CLERK_AUTHORIZED_PARTIES = [p.strip() for p in _AZP_RAW.split(",") if p.strip()]

# Dev escape hatch: accept X-Owner-Id when no real auth is configured. OFF by default.
AUTH_DEV_ALLOW_HEADER = os.environ.get("AUTH_DEV_ALLOW_HEADER", "0") == "1"

_LEEWAY = int(os.environ.get("CLERK_LEEWAY_SECONDS", "30"))

# --- JWKS client (cached, thread-safe, lazily built) -------------------------
_jwk_client: PyJWKClient | None = None
_jwk_lock = threading.Lock()


def _get_jwk_client() -> PyJWKClient:
    global _jwk_client
    if not CLERK_JWKS_URL:
        raise HTTPException(
            status_code=503,
            detail="Auth not configured (CLERK_JWKS_URL unset).",
        )
    with _jwk_lock:
        if _jwk_client is None:
            # PyJWKClient caches keys and refreshes on unknown kid (handles rotation).
            _jwk_client = PyJWKClient(CLERK_JWKS_URL, cache_keys=True, lifespan=3600)
        return _jwk_client


def _verify_bearer(token: str) -> dict[str, Any]:
    """Verify a Clerk session JWT and return its claims. Raises 401 on any failure."""
    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
        options = {"require": ["exp", "sub"], "verify_aud": False}
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options=options,
            leeway=_LEEWAY,
            issuer=CLERK_ISSUER or None,
        )
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status_code=401, detail="Session expired.") from e
    except Exception as e:  # noqa: BLE001 — any verification failure = unauthenticated
        raise HTTPException(status_code=401, detail="Invalid session token.") from e

    # Authorized-parties check (azp) — reject tokens minted for other origins.
    if CLERK_AUTHORIZED_PARTIES:
        azp = claims.get("azp")
        if azp and azp not in CLERK_AUTHORIZED_PARTIES:
            raise HTTPException(status_code=401, detail="Unauthorized party.")

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token missing subject.")
    return claims


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


def current_user_id(
    request: Request,
    authorization: str | None = Header(default=None),
    x_owner_id: str | None = Header(default=None),
) -> str:
    """FastAPI dependency → the authenticated Clerk user id (used as owner_id).

    Order:
      1. Bearer token (Clerk JWT) — the real path. Verified against JWKS.
      2. If AUTH_DEV_ALLOW_HEADER is on and no bearer present, fall back to the
         legacy X-Owner-Id header (dev/tests only).
      3. Otherwise 401.
    """
    token = _extract_bearer(authorization)
    if token:
        claims = _verify_bearer(token)
        return str(claims["sub"])

    if AUTH_DEV_ALLOW_HEADER:
        oid = (x_owner_id or "").strip()
        if oid and len(oid) <= 128:
            return oid

    raise HTTPException(status_code=401, detail="Authentication required.")


def optional_user_id(
    request: Request,
    authorization: str | None = Header(default=None),
    x_owner_id: str | None = Header(default=None),
) -> str | None:
    """Like current_user_id but returns None instead of raising when unauthenticated.

    Used by endpoints that are usable anonymously (e.g. a limited free preview)
    but that unlock more when signed in.
    """
    try:
        return current_user_id(request, authorization, x_owner_id)
    except HTTPException:
        return None
