"""Cost & abuse guards for the public API — no external deps (in-memory, stdlib).

Three layers protect the live link from running up an Opus bill:
  1. TTLCache      — cache expensive LLM results so repeat requests are free.
  2. RateLimiter   — per-key (per-IP) token bucket to stop a single client hammering.
  3. SpendGuard    — global daily USD ceiling; hard-stop when exceeded.

In-memory is intentional for the MVP single-instance Railway deploy. Swap for
Redis when we scale horizontally (the interfaces are deliberately small).
All knobs are env-overridable so we tune in prod without a redeploy.
"""
from __future__ import annotations

import os
import threading
import time

# --- config (env-overridable) ------------------------------------------------
COPILOT_CACHE_TTL = int(os.environ.get("COPILOT_CACHE_TTL", "300"))        # 5 min
SCAN_CACHE_TTL = int(os.environ.get("SCAN_CACHE_TTL", "60"))               # 1 min
COPILOT_RATE_PER_HOUR = int(os.environ.get("COPILOT_RATE_PER_HOUR", "12")) # per IP
DAILY_SPEND_CAP_USD = float(os.environ.get("DAILY_SPEND_CAP_USD", "5.0"))  # hard stop


class TTLCache:
    """Tiny thread-safe TTL cache."""

    def __init__(self, ttl: int):
        self.ttl = ttl
        self._store: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict | None:
        with self._lock:
            hit = self._store.get(key)
            if not hit:
                return None
            ts, val = hit
            if time.time() - ts > self.ttl:
                self._store.pop(key, None)
                return None
            return val

    def set(self, key: str, val: dict):
        with self._lock:
            self._store[key] = (time.time(), val)

    def clear(self):
        with self._lock:
            self._store.clear()


class RateLimiter:
    """Per-key fixed-window limiter (window = 1 hour)."""

    def __init__(self, max_per_hour: int):
        self.max = max_per_hour
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.time()
        window = 3600.0
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < window]
            if len(hits) >= self.max:
                retry = int(window - (now - hits[0])) + 1
                self._hits[key] = hits
                return False, retry
            hits.append(now)
            self._hits[key] = hits
            return True, 0


class SpendGuard:
    """Global daily USD spend ceiling. Resets each UTC day."""

    def __init__(self, cap_usd: float):
        self.cap = cap_usd
        self._day = time.strftime("%Y-%m-%d", time.gmtime())
        self._spent = 0.0
        self._lock = threading.Lock()

    def _roll(self):
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._day:
            self._day = today
            self._spent = 0.0

    def check(self) -> bool:
        """True if under cap (request may proceed)."""
        with self._lock:
            self._roll()
            return self._spent < self.cap

    def add(self, usd: float):
        with self._lock:
            self._roll()
            self._spent += usd

    @property
    def spent_today(self) -> float:
        with self._lock:
            self._roll()
            return round(self._spent, 4)


# --- module-level singletons -------------------------------------------------
copilot_cache = TTLCache(COPILOT_CACHE_TTL)
scan_cache = TTLCache(SCAN_CACHE_TTL)
copilot_limiter = RateLimiter(COPILOT_RATE_PER_HOUR)
spend_guard = SpendGuard(DAILY_SPEND_CAP_USD)


def client_key(request) -> str:
    """Best-effort client identity. Honors X-Forwarded-For (Railway/Vercel proxy)."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
