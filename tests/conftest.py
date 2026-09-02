"""Shared test fixtures.

Makes the plugin importable as ``fishaudio_tts`` without a Hermes install by
stubbing the only Hermes-side dependency (``agent.tts_provider.TTSProvider``)
with a duck-type base class. Everything else (provider.py, normalize.py) is
pure stdlib + requests, so the suite runs anywhere pytest does.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG_NAME = "fishaudio_tts"


def _load_plugin_package():
    """Load provider.py + normalize.py as the ``fishaudio_tts`` package,
    injecting a stub ``agent.tts_provider`` module first."""
    if "agent" not in sys.modules:
        agent_mod = types.ModuleType("agent")
        tts_mod = types.ModuleType("agent.tts_provider")

        class TTSProvider:  # minimal stand-in for the Hermes base class
            pass

        tts_mod.TTSProvider = TTSProvider
        agent_mod.tts_provider = tts_mod
        sys.modules["agent"] = agent_mod
        sys.modules["agent.tts_provider"] = tts_mod

    if _PKG_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _PKG_NAME,
            os.path.join(PLUGIN_DIR, "__init__.py"),
            submodule_search_locations=[PLUGIN_DIR],
        )
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[_PKG_NAME] = pkg
        spec.loader.exec_module(pkg)

    import fishaudio_tts.provider as provider
    import fishaudio_tts.normalize as normalize

    return provider, normalize


provider_mod, normalize_mod = _load_plugin_package()


@pytest.fixture
def provider():
    return provider_mod.FishAudioTTSProvider()


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Remove every FISH_* variable so tests control config from scratch.
    Also points TMPDIR at a per-test dir so the response cache never
    leaks between tests or runs."""
    for var in ("FISH_API_KEY", "FISH_VOICE_ID", "FISH_TTS_MODEL",
                "FISH_TTS_CACHE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    return monkeypatch
