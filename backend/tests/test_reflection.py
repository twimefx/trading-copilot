"""Reflection loop — feed the scored track record back into the model prompt.

The single highest-leverage idea borrowed from TradingAgents: don't just REPORT
past performance, inject the recent, honestly-scored record on THIS symbol into
the copilot/debate prompt so the model reasons with its own recent history.

The reflection text is DETERMINISTIC (built from signal_history rows, no LLM) so
it stays honest and cheap, matching the explainability moat.
"""
import time

import pytest

from backend.signals import history as signal_history
from backend.journal.store import _conn, _q


def _log(symbol, lean, conviction, entry, outcome=None, age_h=30):
    """Log a signal and, if outcome given, mark it resolved `age_h` hours old."""
    sid = signal_history.log_signal(symbol, "1h", "crypto", lean, conviction, entry)
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            _q("UPDATE signal_history SET created_at = ? WHERE id = ?"),
            (time.time() - age_h * 3600, sid),
        )
        if outcome is not None:
            cur.execute(
                _q("UPDATE signal_history SET outcome = ?, resolved_at = ? WHERE id = ?"),
                (outcome, time.time() - (age_h - 24) * 3600, sid),
            )
    return sid


# --- history.reflection(symbol) ------------------------------------------------

def test_reflection_empty_when_no_history():
    assert signal_history.reflection("NOSUCH") is None


def test_reflection_none_when_all_pending():
    _log("PENDUSDT", "bullish", 70, 100.0)  # unresolved
    assert signal_history.reflection("PENDUSDT") is None


def test_reflection_summarizes_scored_record():
    _log("BTCUSDT", "bullish", 70, 100.0, outcome="correct")
    _log("BTCUSDT", "bullish", 65, 100.0, outcome="correct")
    _log("BTCUSDT", "bearish", 60, 100.0, outcome="incorrect")
    _log("BTCUSDT", "neutral", None, 100.0, outcome="flat")
    text = signal_history.reflection("BTCUSDT")
    assert text is not None
    # Mentions the symbol, the scored count, accuracy, and the recent calls.
    assert "BTCUSDT" in text
    assert "3" in text                       # 3 scored directional calls (2 correct 1 incorrect)
    assert "67" in text or "66" in text      # 2/3 accuracy ~ 67%
    assert "bullish" in text and "bearish" in text


def test_reflection_excludes_neutral_from_accuracy():
    _log("ETHUSDT", "neutral", None, 100.0, outcome="flat")
    _log("ETHUSDT", "neutral", None, 100.0, outcome="flat")
    # All flat/neutral -> nothing directional scored -> no reflection.
    assert signal_history.reflection("ETHUSDT") is None


def test_reflection_symbol_case_insensitive():
    _log("SOLUSDT", "bullish", 70, 100.0, outcome="correct")
    assert signal_history.reflection("solusdt") is not None


def test_reflection_flags_weak_record():
    # Mostly wrong -> the text should signal low recent accuracy honestly.
    _log("BADUSDT", "bullish", 70, 100.0, outcome="incorrect")
    _log("BADUSDT", "bullish", 70, 100.0, outcome="incorrect")
    _log("BADUSDT", "bullish", 70, 100.0, outcome="correct")
    text = signal_history.reflection("BADUSDT")
    assert text is not None
    assert "33" in text  # 1/3 = 33%


# --- prompt injection (copilot + debate) ---------------------------------------

class _FakeRouter:
    """Captures the last prompt/system; returns minimal valid JSON per call."""

    def __init__(self, payload='{"lean":"neutral","conviction":50,"summary":"s","drivers":[],"risks":[]}'):
        self.prompts = []
        self.systems = []
        self._payload = payload
        from backend.ai.router import AIRouter
        self.cost_log = AIRouter.__new__(AIRouter)  # only need .cost_log.total_usd
        class _CL: total_usd = 0.0
        self.cost_log = _CL()

    def complete(self, task, prompt, *, system=None, max_tokens=1024):
        self.prompts.append(prompt)
        self.systems.append(system)
        return self._payload


def _ctx(symbol="BTCUSDT"):
    """A minimal MarketContext good enough for prompt construction."""
    from backend.signals.context import MarketContext
    return MarketContext(symbol=symbol, interval="1h", indicators={"last_close": 100.0})


def test_copilot_prompt_includes_reflection_when_record_exists():
    from backend.signals import copilot
    _log("BTCUSDT", "bullish", 70, 100.0, outcome="correct")
    router = _FakeRouter()
    copilot.analyze(_ctx("BTCUSDT"), router=router)
    assert any("Track record for BTCUSDT" in p for p in router.prompts)


def test_copilot_prompt_omits_reflection_when_no_record():
    from backend.signals import copilot
    router = _FakeRouter()
    copilot.analyze(_ctx("NOSUCH"), router=router)
    assert all("Track record" not in p for p in router.prompts)


def test_debate_agent_prompt_includes_reflection():
    from backend.signals import agents
    _log("BTCUSDT", "bullish", 70, 100.0, outcome="correct")
    router = _FakeRouter('{"lean":"bullish","conviction":60,"rationale":"r","key_evidence":[]}')
    agents.run_agent(agents.AGENTS[0], _ctx("BTCUSDT"), router)
    assert any("Track record for BTCUSDT" in p for p in router.prompts)


def test_copilot_result_exposes_reflection_text():
    from backend.signals import copilot
    _log("BTCUSDT", "bullish", 70, 100.0, outcome="correct")
    result = copilot.analyze(_ctx("BTCUSDT"), router=_FakeRouter())
    assert result.get("track_record") is not None
    assert "BTCUSDT" in result["track_record"]


def test_copilot_result_track_record_none_when_no_history():
    from backend.signals import copilot
    result = copilot.analyze(_ctx("NOSUCH"), router=_FakeRouter())
    assert result.get("track_record") is None
