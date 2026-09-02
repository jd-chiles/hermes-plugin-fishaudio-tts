"""Fish Audio TTS provider for Hermes.

Design goals (per user request):
  * LOW LATENCY — synthesize sentence-by-sentence with Fish's `latency:
    balanced` mode and a small `chunk_length` so the first audio arrives
    fast, and so long technical replies don't block on one giant request.
  * NATURAL VOICE — run the reply through `normalize_for_speech` first so
    fenced/inline code and markdown are NOT read out loud; the chat still
    shows the raw text, only the spoken audio is cleaned.
  * FAST TRANSCRIPTION — the transcription side is configured separately
    (STT), see plugin README; this provider only handles synthesis.

Endpoint: POST https://api.fish.audio/v1/tts  (JSON body, raw audio bytes)
Auth:     Bearer FISH_API_KEY
Model:    s2.1-pro-free by default (free developer tier)
"""
from __future__ import annotations

import logging
import os
import re

import requests

from agent.tts_provider import TTSProvider

from .normalize import normalize_for_speech

logger = logging.getLogger(__name__)

_FISH_URL = "https://api.fish.audio/v1/tts"
_DEFAULT_MODEL = "s2.1-pro-free"
# Split on whitespace that follows sentence-ending punctuation so each
# synthesis request is short (faster first-audio, lower perceived latency).
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Latency tuning: `balanced` gives ~300ms time-to-first-audio; a small
# chunk_length starts generating sooner. `normalize` reads numbers/dates
# naturally so the spoken reply sounds human, not robotic.
_SYNTH_KWARGS = {
    "latency": "balanced",
    "chunk_length": 120,
    "normalize": True,
}


class FishAudioTTSProvider(TTSProvider):
    """Fish Audio backend registered as ``tts.provider: fishaudio``."""

    @property
    def name(self) -> str:
        return "fishaudio"

    @property
    def display_name(self) -> str:
        return "Fish Audio"

    def is_available(self) -> bool:
        # Always "available" if the key exists; missing key surfaces a
        # clear error at synthesis time rather than crashing discovery.
        return bool(os.environ.get("FISH_API_KEY"))

    def get_setup_schema(self) -> dict:
        return {
            "name": "Fish Audio",
            "badge": "free-dev",
            "tag": "s2.1-pro-free via Fish Audio API",
            "env_vars": [
                {
                    "key": "FISH_API_KEY",
                    "prompt": "Fish Audio API key",
                    "url": "https://fish.audio/app/api-keys/",
                },
                {
                    "key": "FISH_VOICE_ID",
                    "prompt": "Fish Audio voice model id (optional; omit for default voice)",
                    "url": "https://fish.audio/app/voices/",
                },
                {
                    "key": "FISH_TTS_MODEL",
                    "prompt": "Fish Audio TTS model (optional; default s2.1-pro-free)",
                    "url": "https://docs.fish.audio/developer-guide/models-pricing/models-overview",
                },
            ],
        }

    @property
    def voice_compatible(self) -> bool:
        # Opt in so the gateway converts mp3 -> opus for voice-bubble
        # delivery (Telegram et al.) when replying with a voice message.
        return True

    # -- internal helpers --------------------------------------------------

    def _resolve(self, voice, model):
        voice_id = voice or os.environ.get("FISH_VOICE_ID") or None
        model_id = model or os.environ.get("FISH_TTS_MODEL") or _DEFAULT_MODEL
        return voice_id, model_id

    def _synth_one(self, api_key, model, voice_id, text, fmt, speed) -> bytes:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "model": model,
        }
        body = {
            "text": text,
            "format": fmt,
            **_SYNTH_KWARGS,
        }
        if voice_id:
            body["reference_id"] = voice_id
        if speed is not None:
            body["prosody"] = {"speed": float(speed)}
        try:
            resp = requests.post(
                _FISH_URL, headers=headers, json=body, timeout=60
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Fish Audio request failed: {exc}") from exc
        if resp.status_code != 200:
            snippet = resp.text[:200]
            raise RuntimeError(
                f"Fish Audio TTS error {resp.status_code}: {snippet}"
            )
        return resp.content

    # -- required interface ------------------------------------------------

    def synthesize(
        self,
        text: str,
        output_path: str,
        *,
        voice: str | None = None,
        model: str | None = None,
        speed: float | None = None,
        format: str = "mp3",
        **extra,
    ) -> str:
        api_key = os.environ.get("FISH_API_KEY")
        if not api_key:
            raise RuntimeError(
                "FISH_API_KEY is not set. Add it to ~/.hermes/.env and restart."
            )

        voice_id, model_id = self._resolve(voice, model)

        # Clean the text for speech (drops code/markdown, expands abbr).
        spoken = normalize_for_speech(text)
        if not spoken.strip():
            spoken = "Okay."

        # Sentence-chunked synthesis: small requests -> faster first audio
        # and no single giant request blocking the whole reply.
        sentences = [s.strip() for s in _SENT_SPLIT.split(spoken) if s.strip()]
        if not sentences:
            sentences = [spoken]

        parts: list[bytes] = []
        for sent in sentences:
            parts.append(
                self._synth_one(api_key, model_id, voice_id, sent, format, speed)
            )

        with open(output_path, "wb") as fh:
            for chunk in parts:
                fh.write(chunk)
        return output_path
