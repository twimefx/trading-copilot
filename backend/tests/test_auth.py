"""Tests for Clerk JWT verification, using a locally-generated RSA keypair.

We mint tokens with a test private key and point the verifier at a JWKS built
from the matching public key — so we exercise the real signature/claims path
without a live Clerk instance.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import backend.api.auth as auth


@pytest.fixture()
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key


def _make_token(key, claims, kid="testkid"):
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


class _FakeSigningKey:
    def __init__(self, pub):
        self.key = pub


@pytest.fixture()
def wire_verifier(keypair, monkeypatch):
    """Point the auth module's JWKS lookup at our in-memory public key."""
    pub = keypair.public_key()

    class _FakeJWKClient:
        def get_signing_key_from_jwt(self, token):
            return _FakeSigningKey(pub)

    monkeypatch.setattr(auth, "_get_jwk_client", lambda: _FakeJWKClient())
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)        # exercise the real auth path
    monkeypatch.setattr(auth, "CLERK_ISSUER", "")           # skip issuer check
    monkeypatch.setattr(auth, "CLERK_AUTHORIZED_PARTIES", [])
    return keypair


def _call(authorization=None, x_owner_id=None):
    class _Req:
        headers = {}
        client = None
    return auth.current_user_id(_Req(), authorization, x_owner_id)


def test_valid_token_yields_subject(wire_verifier):
    token = _make_token(wire_verifier, {"sub": "user_abc", "exp": int(time.time()) + 300})
    assert _call(authorization=f"Bearer {token}") == "user_abc"


def test_expired_token_rejected(wire_verifier):
    token = _make_token(wire_verifier, {"sub": "user_abc", "exp": int(time.time()) - 120})
    with pytest.raises(auth.HTTPException) as e:
        _call(authorization=f"Bearer {token}")
    assert e.value.status_code == 401


def test_missing_auth_rejected(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTH_DEV_ALLOW_HEADER", False)
    with pytest.raises(auth.HTTPException) as e:
        _call()
    assert e.value.status_code == 401


def test_dev_header_fallback_when_enabled(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTH_DEV_ALLOW_HEADER", True)
    assert _call(x_owner_id="legacy-owner") == "legacy-owner"


def test_dev_header_ignored_when_disabled(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "AUTH_DEV_ALLOW_HEADER", False)
    with pytest.raises(auth.HTTPException):
        _call(x_owner_id="legacy-owner")


def test_authorized_party_enforced(wire_verifier, monkeypatch):
    monkeypatch.setattr(auth, "CLERK_AUTHORIZED_PARTIES", ["https://good.app"])
    token = _make_token(wire_verifier,
                        {"sub": "u", "exp": int(time.time()) + 300, "azp": "https://evil.app"})
    with pytest.raises(auth.HTTPException) as e:
        _call(authorization=f"Bearer {token}")
    assert e.value.status_code == 401


def test_open_mode_returns_anon_owner_when_auth_disabled(monkeypatch):
    """When Clerk isn't configured, the API degrades to anonymous (no 401) so a
    deploy of the auth code can't break a prod instance lacking Clerk keys."""
    monkeypatch.setattr(auth, "AUTH_ENABLED", False)
    monkeypatch.setattr(auth, "ANON_OWNER_ID", "public")
    # No token, no dev header — still resolves to the shared anonymous owner.
    assert _call() == "public"

