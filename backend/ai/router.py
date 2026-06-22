"""Provider-agnostic LLM router — Tim's key requirement.

Routes each task class to the cheapest model that meets quality needs, logs token
cost per call, and stays swappable so we never get locked into one vendor.

Heavy reasoning  -> Claude Opus/Sonnet  (best quality)
Mid structured   -> GPT-class / Hermes  (fast, cheap-ish)
Bulk scans       -> DeepSeek            (cheapest at scale)

Phase 0: Anthropic provider only (verifies the architecture). OpenAI/DeepSeek
providers slot in behind the same `complete()` interface in Phase 1+.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("ai.router")


class TaskClass(str, Enum):
    """Maps product features to a routing tier."""
    MARKET_COPILOT = "market_copilot"
    TRADE_EXPLANATION = "trade_explanation"
    STRATEGY_BUILDER = "strategy_builder"
    AGENT_CONSENSUS = "agent_consensus"
    SIGNAL_SUMMARY = "signal_summary"
    MARKET_SCAN = "market_scan"
    DEFAULT = "default"


@dataclass(frozen=True)
class ModelChoice:
    provider: str       # "anthropic" | "openai" | "deepseek"
    model: str
    # rough per-1M-token USD pricing for cost logging (update as pricing changes)
    in_price: float
    out_price: float


# --- Routing table -----------------------------------------------------------
# Premium reasoning -> Claude. Cheap bulk -> DeepSeek. Mid -> GPT-class.
ROUTING: dict[TaskClass, ModelChoice] = {
    TaskClass.MARKET_COPILOT:    ModelChoice("anthropic", "claude-opus-4-20250514", 15.0, 75.0),
    TaskClass.TRADE_EXPLANATION: ModelChoice("anthropic", "claude-opus-4-20250514", 15.0, 75.0),
    TaskClass.STRATEGY_BUILDER:  ModelChoice("anthropic", "claude-opus-4-20250514", 15.0, 75.0),
    TaskClass.AGENT_CONSENSUS:   ModelChoice("anthropic", "claude-opus-4-20250514", 15.0, 75.0),
    TaskClass.SIGNAL_SUMMARY:    ModelChoice("anthropic", "claude-sonnet-4-20250514", 3.0, 15.0),
    TaskClass.MARKET_SCAN:       ModelChoice("deepseek", "deepseek-chat", 0.27, 1.10),
    TaskClass.DEFAULT:           ModelChoice("anthropic", "claude-sonnet-4-20250514", 3.0, 15.0),
}


@dataclass
class CostLog:
    """Accumulates spend so we can watch the budget as the platform scales."""
    total_usd: float = 0.0
    calls: list[dict] = field(default_factory=list)

    def record(self, task: TaskClass, choice: ModelChoice, in_tok: int, out_tok: int):
        cost = (in_tok / 1e6) * choice.in_price + (out_tok / 1e6) * choice.out_price
        self.total_usd += cost
        entry = {
            "task": task.value, "provider": choice.provider, "model": choice.model,
            "in_tok": in_tok, "out_tok": out_tok, "usd": round(cost, 6),
        }
        self.calls.append(entry)
        logger.info("LLM %s/%s task=%s in=%d out=%d cost=$%.5f total=$%.4f",
                    choice.provider, choice.model, task.value, in_tok, out_tok, cost, self.total_usd)
        return cost


class AIRouter:
    def __init__(self, cost_log: CostLog | None = None):
        self.cost_log = cost_log or CostLog()
        self._anthropic = None

    def _anthropic_client(self):
        if self._anthropic is None:
            import anthropic
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            self._anthropic = anthropic.Anthropic(api_key=key)
        return self._anthropic

    def complete(self, task: TaskClass, prompt: str, *, max_tokens: int = 1024,
                 system: str | None = None) -> str:
        """Route `prompt` to the right model for `task`, log cost, return text."""
        choice = ROUTING.get(task, ROUTING[TaskClass.DEFAULT])

        if choice.provider == "anthropic":
            client = self._anthropic_client()
            kwargs = {"model": choice.model, "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]}
            if system:
                kwargs["system"] = system
            msg = client.messages.create(**kwargs)
            text = msg.content[0].text
            self.cost_log.record(task, choice, msg.usage.input_tokens, msg.usage.output_tokens)
            return text

        # Phase 1+: openai / deepseek providers behind the same interface.
        raise NotImplementedError(f"Provider '{choice.provider}' not wired yet (Phase 1+)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    router = AIRouter()
    out = router.complete(TaskClass.SIGNAL_SUMMARY, "Reply with exactly: ROUTER_OK", max_tokens=20)
    print("Router response:", out)
    print(f"Total spend this run: ${router.cost_log.total_usd:.6f}")
