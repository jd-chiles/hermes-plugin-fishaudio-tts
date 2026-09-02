"""Fish Audio TTS plugin entry point.

Hermes loads this module and calls ``register(ctx)`` once. We register a
``fishaudio`` TTS provider so that ``tts.provider: fishaudio`` in
config.yaml routes every ``text_to_speech`` call to Fish Audio's API.
"""
from __future__ import annotations

from .provider import FishAudioTTSProvider


def register(ctx) -> None:
    """Register the Fish Audio TTS provider. Called by the plugin loader."""
    ctx.register_tts_provider(FishAudioTTSProvider())
