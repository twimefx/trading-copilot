"""Tests for Kronos device selection (cpu default, cuda on GPU host)."""
import importlib

import pytest

from backend.signals import kronos_range


def test_device_defaults_to_cpu(monkeypatch):
    monkeypatch.delenv("KRONOS_DEVICE", raising=False)
    assert kronos_range._device() == "cpu"


def test_device_reads_env(monkeypatch):
    monkeypatch.setenv("KRONOS_DEVICE", "cuda")
    assert kronos_range._device() == "cuda"


def test_predictor_uses_selected_device(monkeypatch):
    """The singleton predictor must be built with the env-selected device."""
    monkeypatch.setenv("KRONOS_DEVICE", "cuda")
    captured = {}

    class FakePredictor:
        def __init__(self, model, tokenizer, max_context, device):
            captured["device"] = device

    class FakeModel:
        @classmethod
        def from_pretrained(cls, name):
            return cls()

    class FakeTokenizer(FakeModel):
        pass

    import sys
    import types
    fake_mod = types.ModuleType("model")
    fake_mod.Kronos = FakeModel
    fake_mod.KronosTokenizer = FakeTokenizer
    fake_mod.KronosPredictor = FakePredictor
    monkeypatch.setitem(sys.modules, "model", fake_mod)
    monkeypatch.setattr(kronos_range, "_predictor", None)  # reset singleton

    kronos_range._get_predictor()
    assert captured["device"] == "cuda"
