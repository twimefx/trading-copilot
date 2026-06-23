"""Tests for the cost & abuse guards (cache, rate limit, spend cap)."""
import time

from backend.api.guards import RateLimiter, SpendGuard, TTLCache


def test_ttl_cache_hit_and_expiry():
    c = TTLCache(ttl=1)
    c.set("k", {"v": 1})
    assert c.get("k") == {"v": 1}      # hit
    time.sleep(1.1)
    assert c.get("k") is None          # expired


def test_rate_limiter_blocks_after_max():
    rl = RateLimiter(max_per_hour=3)
    for _ in range(3):
        allowed, _ = rl.allow("1.2.3.4")
        assert allowed
    allowed, retry = rl.allow("1.2.3.4")
    assert not allowed
    assert retry > 0


def test_rate_limiter_is_per_key():
    rl = RateLimiter(max_per_hour=1)
    assert rl.allow("ip-a")[0] is True
    assert rl.allow("ip-a")[0] is False   # a is now blocked
    assert rl.allow("ip-b")[0] is True     # b is independent


def test_spend_guard_caps():
    g = SpendGuard(cap_usd=0.10)
    assert g.check() is True
    g.add(0.06)
    assert g.check() is True               # 0.06 < 0.10
    g.add(0.06)
    assert g.check() is False              # 0.12 >= 0.10
    assert g.spent_today == 0.12
