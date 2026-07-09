"""Provider-agnostic LLM router — Tim's key requirement.

Routes each task class to the cheapest model that meets quality needs, logs token
cost per call, and stays swappable so we never get locked into one vendor.

Heavy reasoning  -> Claude Opus/Sonnet  (best quality)
Mid structured   -> GPT-class            (fast, cheap-ish)
Bulk scans       -> DeepSeek             (cheapest at scale)

Phase 1: three providers wired behind one `complete()` interface —
  * anthropic  (native SDK)
  * openai     (native SDK)
  * deepseek   (OpenAI-compatible SDK, base_url override)

Each provider activates only when its API key env var is set. If a route points
at a provider whose key is missing, we FALL BACK to Anthropic (the always-on
premium provider) rather than erroring — so the product never goes dark just
because DeepSeek isn't configured yet. The fallback is logged so cost drift is visible.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("ai.router")

# DeepSeek speaks the OpenAI wire protocol; only the base URL differs.
DEEPSEEK_BASE_URL = "https://api.deepseek.com"


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
# Model ids are env-overridable (MODEL_<TASK>) so we can retune in prod without a
# redeploy when a provider ships a newer/cheaper model or deprecates an id.
ROUTING: dict[TaskClass, ModelChoice] = {
    TaskClass.MARKET_COPILOT:    ModelChoice("anthropic", "claude-opus-4-8", 15.0, 75.0),
    TaskClass.TRADE_EXPLANATION: ModelChoice("anthropic", "claude-opus-4-8", 15.0, 75.0),
    TaskClass.STRATEGY_BUILDER:  ModelChoice("anthropic", "claude-opus-4-8", 15.0, 75.0),
    TaskClass.AGENT_CONSENSUS:   ModelChoice("anthropic", "claude-opus-4-8", 15.0, 75.0),
    TaskClass.SIGNAL_SUMMARY:    ModelChoice("openai", "gpt-4o-mini", 0.15, 0.60),
    TaskClass.MARKET_SCAN:       ModelChoice("deepseek", "deepseek-chat", 0.27, 1.10),
    # Cheap default: DeepSeek when configured, else falls back to Anthropic Haiku.
    TaskClass.DEFAULT:           ModelChoice("anthropic", "claude-haiku-4-5-20251001", 1.0, 5.0),
}

# Env var that carries each provider's credential. Missing key => provider disabled.
_PROVIDER_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

# Fallback when a routed provider is unconfigured. Anthropic is the always-on
# premium provider (its key is required for the product to function at all).
_FALLBACK = ModelChoice("anthropic", "claude-haiku-4-5-20251001", 1.0, 5.0)


def _provider_configured(provider: str) -> bool:
    env = _PROVIDER_KEY_ENV.get(provider)
    return bool(env and os.environ.get(env, "").strip())


def _resolve_model(task: TaskClass, choice: ModelChoice) -> str:
    """Allow a MODEL_<TASK> env override of the model id (prod retuning w/o deploy)."""
    return os.environ.get(f"MODEL_{task.name}", "").strip() or choice.model


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
        self._openai = None
        self._deepseek = None

    # --- lazy provider clients (import + construct on first use) --------------
    def _anthropic_client(self):
        if self._anthropic is None:
            import anthropic
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            self._anthropic = anthropic.Anthropic(api_key=key)
        return self._anthropic

    def _openai_client(self):
        if self._openai is None:
            from openai import OpenAI
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise RuntimeError("OPENAI_API_KEY not set")
            self._openai = OpenAI(api_key=key)
        return self._openai

    def _deepseek_client(self):
        if self._deepseek is None:
            from openai import OpenAI  # DeepSeek is OpenAI-wire-compatible
            key = os.environ.get("DEEPSEEK_API_KEY")
            if not key:
                raise RuntimeError("DEEPSEEK_API_KEY not set")
            base = os.environ.get("DEEPSEEK_BASE_URL", DEEPSEEK_BASE_URL)
            self._deepseek = OpenAI(api_key=key, base_url=base)
        return self._deepseek

    # --- per-provider completion (uniform text-in/text-out) ------------------
    def _complete_anthropic(self, choice: ModelChoice, model: str, prompt: str,
                            max_tokens: int, system: str | None) -> tuple[str, int, int]:
        client = self._anthropic_client()
        kwargs = {"model": model, "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]}
        if system:
            kwargs["system"] = system
        msg = client.messages.create(**kwargs)
        return msg.content[0].text, msg.usage.input_tokens, msg.usage.output_tokens

    def _complete_openai_compatible(self, client, model: str, prompt: str,
                                    max_tokens: int, system: str | None) -> tuple[str, int, int]:
        """Shared path for OpenAI and DeepSeek (identical chat.completions API)."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens, messages=messages,
        )
        text = resp.choices[0].message.content or ""
        usage = resp.usage
        in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        return text, in_tok, out_tok

    def _dispatch(self, choice: ModelChoice, model: str, prompt: str,
                  max_tokens: int, system: str | None) -> tuple[str, int, int]:
        """Call the provider for `choice`. Raises on provider/network error."""
        if choice.provider == "anthropic":
            return self._complete_anthropic(choice, model, prompt, max_tokens, system)
        elif choice.provider == "openai":
            return self._complete_openai_compatible(
                self._openai_client(), model, prompt, max_tokens, system)
        elif choice.provider == "deepseek":
            return self._complete_openai_compatible(
                self._deepseek_client(), model, prompt, max_tokens, system)
        raise NotImplementedError(f"Provider '{choice.provider}' not supported")

    def complete(self, task: TaskClass, prompt: str, *, max_tokens: int = 1024,
                 system: str | None = None) -> str:
        """Route `prompt` to the right model for `task`, log cost, return text.

        Resilience: if the routed provider has no API key configured, OR its call
        fails at runtime (quota exhausted, rate limit, transient network error),
        we transparently fall back to the always-on Anthropic provider so the
        feature keeps working. Both fallback paths are logged so cost/health drift
        is visible. This matters in practice — e.g. a valid OpenAI key with no
        billing credits returns HTTP 429 insufficient_quota, which must NOT take
        the feature down.
        """
        choice = ROUTING.get(task, ROUTING[TaskClass.DEFAULT])

        if not _provider_configured(choice.provider):
            logger.warning(
                "provider '%s' for task=%s unconfigured (%s missing) -> falling back to %s/%s",
                choice.provider, task.value,
                _PROVIDER_KEY_ENV.get(choice.provider, "?"),
                _FALLBACK.provider, _FALLBACK.model,
            )
            choice = _FALLBACK

        model = _resolve_model(task, choice)

        try:
            text, in_tok, out_tok = self._dispatch(choice, model, prompt, max_tokens, system)
        except Exception as e:  # noqa: BLE001 — any provider failure => try the fallback
            # Don't loop if we already ARE the fallback provider (e.g. Anthropic down).
            if choice.provider == _FALLBACK.provider and model == _FALLBACK.model:
                logger.error("fallback provider %s/%s also failed for task=%s: %s",
                             choice.provider, model, task.value, e)
                raise
            logger.warning(
                "provider '%s' failed for task=%s (%s: %s) -> falling back to %s/%s",
                choice.provider, task.value, type(e).__name__, str(e)[:120],
                _FALLBACK.provider, _FALLBACK.model,
            )
            choice = _FALLBACK
            model = _resolve_model(task, choice)
            text, in_tok, out_tok = self._dispatch(choice, model, prompt, max_tokens, system)

        # Cost log reflects the model actually used (fallback price if we fell back).
        self.cost_log.record(task, ModelChoice(choice.provider, model,
                                                choice.in_price, choice.out_price),
                             in_tok, out_tok)
        return text


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    router = AIRouter()
    out = router.complete(TaskClass.SIGNAL_SUMMARY, "Reply with exactly: ROUTER_OK", max_tokens=20)
    print("Router response:", out)
    print(f"Total spend this run: ${router.cost_log.total_usd:.6f}")
