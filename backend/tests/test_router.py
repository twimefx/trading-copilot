"""Tests for the multi-provider LLM router (Phase 1).

These exercise routing, provider selection, cost logging, env overrides, and the
graceful fallback to Anthropic when a routed provider is unconfigured — all with
the provider SDK clients mocked, so no live API keys or network are needed.
"""
from __future__ import annotations

import types

import pytest

from backend.ai.router import (
    AIRouter,
    ModelChoice,
    TaskClass,
    _provider_configured,
    _resolve_model,
)


# --- fake SDK clients --------------------------------------------------------

class _FakeAnthropicMsg:
    def __init__(self, text, in_tok, out_tok):
        self.content = [types.SimpleNamespace(text=text)]
        self.usage = types.SimpleNamespace(input_tokens=in_tok, output_tokens=out_tok)


class _FakeAnthropicClient:
    def __init__(self):
        self.calls = []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeAnthropicMsg("ANTHROPIC_OK", 100, 20)


class _FakeChoice:
    def __init__(self, content):
        self.message = types.SimpleNamespace(content=content)


class _FakeOAResp:
    def __init__(self, content, in_tok, out_tok):
        self.choices = [_FakeChoice(content)]
        self.usage = types.SimpleNamespace(prompt_tokens=in_tok, completion_tokens=out_tok)


class _FakeOAClient:
    def __init__(self, tag):
        self.tag = tag
        self.calls = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeOAResp(f"{self.tag}_OK", 50, 10)


def _router_with_fakes(monkeypatch, anthropic=True, openai=True, deepseek=True):
    """Build an AIRouter with provider client factories swapped for fakes."""
    r = AIRouter()
    fa, fo, fd = _FakeAnthropicClient(), _FakeOAClient("OPENAI"), _FakeOAClient("DEEPSEEK")
    if anthropic:
        monkeypatch.setattr(r, "_anthropic_client", lambda: fa)
    if openai:
        monkeypatch.setattr(r, "_openai_client", lambda: fo)
    if deepseek:
        monkeypatch.setattr(r, "_deepseek_client", lambda: fd)
    return r, fa, fo, fd


# --- provider configuration + env overrides ---------------------------------

def test_provider_configured_reads_env(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert _provider_configured("deepseek") is False
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert _provider_configured("deepseek") is True


def test_resolve_model_env_override(monkeypatch):
    choice = ModelChoice("deepseek", "deepseek-chat", 0.27, 1.10)
    assert _resolve_model(TaskClass.MARKET_SCAN, choice) == "deepseek-chat"
    monkeypatch.setenv("MODEL_MARKET_SCAN", "deepseek-reasoner")
    assert _resolve_model(TaskClass.MARKET_SCAN, choice) == "deepseek-reasoner"


# --- routing to the right provider -------------------------------------------

def test_copilot_routes_to_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    r, fa, fo, fd = _router_with_fakes(monkeypatch)
    out = r.complete(TaskClass.MARKET_COPILOT, "hi", system="sys")
    assert out == "ANTHROPIC_OK"
    assert len(fa.calls) == 1 and not fo.calls and not fd.calls
    assert fa.calls[0]["system"] == "sys"
    # cost logged against anthropic opus pricing
    assert r.cost_log.calls[0]["provider"] == "anthropic"


def test_scan_routes_to_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    r, fa, fo, fd = _router_with_fakes(monkeypatch)
    out = r.complete(TaskClass.MARKET_SCAN, "screen these", system="sys")
    assert out == "DEEPSEEK_OK"
    assert len(fd.calls) == 1 and not fa.calls
    # system prompt becomes a system-role message in the OpenAI-compatible path
    assert fd.calls[0]["messages"][0] == {"role": "system", "content": "sys"}
    assert r.cost_log.calls[0]["provider"] == "deepseek"


def test_signal_summary_routes_to_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oa")
    r, fa, fo, fd = _router_with_fakes(monkeypatch)
    out = r.complete(TaskClass.SIGNAL_SUMMARY, "summarize")
    assert out == "OPENAI_OK"
    assert len(fo.calls) == 1
    assert r.cost_log.calls[0]["provider"] == "openai"


# --- graceful fallback when a provider is unconfigured -----------------------

def test_deepseek_unconfigured_falls_back_to_anthropic(monkeypatch):
    # No DeepSeek key -> MARKET_SCAN must fall back to Anthropic, not error.
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    r, fa, fo, fd = _router_with_fakes(monkeypatch)
    out = r.complete(TaskClass.MARKET_SCAN, "screen")
    assert out == "ANTHROPIC_OK"          # served by the fallback provider
    assert len(fa.calls) == 1 and not fd.calls
    assert r.cost_log.calls[0]["provider"] == "anthropic"


def test_openai_unconfigured_falls_back(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    r, fa, fo, fd = _router_with_fakes(monkeypatch)
    out = r.complete(TaskClass.SIGNAL_SUMMARY, "summarize")
    assert out == "ANTHROPIC_OK"
    assert len(fa.calls) == 1 and not fo.calls


def test_cost_accumulates_across_calls(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    r, fa, fo, fd = _router_with_fakes(monkeypatch)
    r.complete(TaskClass.MARKET_COPILOT, "a")
    r.complete(TaskClass.MARKET_SCAN, "b")
    assert len(r.cost_log.calls) == 2
    assert r.cost_log.total_usd > 0
