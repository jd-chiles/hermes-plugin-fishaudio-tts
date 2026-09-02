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

Endpoints (verified against https://docs.fish.audio):
  * TTS:     POST https://api.fish.audio/v1/tts (JSON body, raw audio bytes;
             model selected via the `model` header)
  * Voices:  GET  https://api.fish.audio/model — paginated voice-model
             catalog (query params `page_size` (1..100, default 10) and
             `page_number` (>=1); response `{total, items: [{_id, title,
             languages, samples, ...}], has_more, ...}`).
             https://docs.fish.audio/api-reference/endpoint/model/list-models
  * Models:  Fish has no REST endpoint listing TTS model *strings*; the
             known models are documented at
             https://docs.fish.audio/developer-guide/models-pricing/models-overview
             so `list_models()` returns a static, docs-sourced list.

Auth:     Bearer FISH_API_KEY
Model:    s2.1-pro-free by default (free developer tier)
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import tempfile
import time

import requests

from agent.tts_provider import TTSProvider

from .normalize import normalize_for_speech

logger = logging.getLogger(__name__)

_FISH_URL = "https://api.fish.audio/v1/tts"
_FISH_MODELS_URL = "https://api.fish.audio/model"
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

# -- retry / cache tuning ---------------------------------------------------
_MAX_ATTEMPTS = 3               # 1 try + 2 retries
_BACKOFF_BASE = 0.5             # seconds; exponential: 0.5, 1.0
_RETRY_AFTER_CAP = 30           # never sleep longer than this on Retry-After
_VOICES_TTL = 600               # in-process voice-catalog cache: 10 minutes
_VOICES_PAGE_SIZE = 100         # docs: 1..100
_VOICES_MAX_PAGES = 5           # cap pagination at 500 voices
_CACHE_TTL = 24 * 3600          # response cache: 24 hours
def _cache_dir() -> str:
    """Response-cache directory under the system temp dir (TMPDIR-aware)."""
    base = os.environ.get("TMPDIR") or tempfile.gettempdir()
    return os.path.join(base, "fishaudio-tts-cache")

# TTS model strings from https://docs.fish.audio/developer-guide/models-pricing/
# models-overview (no REST endpoint exposes these; static per docs).
_TTS_MODELS = [
    {"id": "s2.1-pro-free", "display": "S2.1-Pro Free (dev tier)"},
    {"id": "s2.1-pro", "display": "S2.1-Pro (production)"},
    {"id": "s2-pro", "display": "S2-Pro (previous generation)"},
    {"id": "s1", "display": "S1 (legacy, 13 languages)"},
]


class FishAudioTTSProvider(TTSProvider):
    """Fish Audio backend registered as ``tts.provider: fishaudio``."""

    def __init__(self) -> None:
        self._voices_cache: list[dict] | None = None
        self._voices_cached_at: float = 0.0

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

    # -- catalogs -----------------------------------------------------------

    def list_models(self) -> list[dict]:
        """Static, docs-sourced TTS model list (Fish has no models-of-record
        endpoint for TTS model strings). Default first."""
        return [dict(m) for m in _TTS_MODELS]

    def list_voices(self) -> list[dict]:
        """Voice catalog from GET /model (paginated), mapped to the ABC
        entry shape. Cached in-process for ~10 minutes; returns [] on any
        API failure (logged, never raised into the caller)."""
        now = time.monotonic()
        if self._voices_cache is not None and now - self._voices_cached_at < _VOICES_TTL:
            return [dict(v) for v in self._voices_cache]

        api_key = os.environ.get("FISH_API_KEY")
        if not api_key:
            logger.debug("fishaudio.list_voices: no FISH_API_KEY; returning []")
            return []

        voices: list[dict] = []
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            for page in range(1, _VOICES_MAX_PAGES + 1):
                resp = requests.get(
                    _FISH_MODELS_URL,
                    headers=headers,
                    params={"page_size": _VOICES_PAGE_SIZE, "page_number": page},
                    timeout=30,
                )
                if resp.status_code != 200:
                    raise RuntimeError(
                        f"voice catalog HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                data = resp.json()
                for item in data.get("items", []):
                    entry: dict = {"id": item.get("_id")}
                    if item.get("title"):
                        entry["display"] = item["title"]
                    langs = item.get("languages") or []
                    if langs:
                        entry["language"] = langs[0]
                    samples = item.get("samples") or []
                    if samples and samples[0].get("filename"):
                        # Best-effort preview URL from the documented sample
                        # attachment layout on the voice model.
                        entry["preview_url"] = (
                            f"https://api.fish.audio/model/"
                            f"{item.get('_id')}/{samples[0]['filename']}"
                        )
                    if entry["id"]:
                        voices.append(entry)
                if not data.get("has_more"):
                    break
        except Exception as exc:  # noqa: BLE001 — never raise into the caller
            logger.warning("fishaudio.list_voices failed, returning []: %s", exc)
            return []

        self._voices_cache = voices
        self._voices_cached_at = now
        return [dict(v) for v in voices]

    # -- internal helpers ---------------------------------------------------

    def _resolve(self, voice, model):
        voice_id = voice or os.environ.get("FISH_VOICE_ID") or None
        model_id = model or os.environ.get("FISH_TTS_MODEL") or _DEFAULT_MODEL
        return voice_id, model_id

    @staticmethod
    def _cache_enabled() -> bool:
        return os.environ.get("FISH_TTS_CACHE", "1") != "0"

    @staticmethod
    def _cache_key(text, voice_id, model, fmt, speed) -> str:
        raw = "|".join(
            [text, voice_id or "", model or "", fmt or "", str(speed or "")]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _cache_read(key: str) -> bytes | None:
        """Best-effort read; any failure returns None (cache is a hint)."""
        try:
            path = os.path.join(_cache_dir(), key)
            st = os.stat(path)
            if time.time() - st.st_mtime > _CACHE_TTL:
                return None
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    @staticmethod
    def _cache_write(key: str, audio: bytes) -> None:
        """Best-effort write; failures are non-fatal."""
        try:
            cache_dir = _cache_dir()
            os.makedirs(cache_dir, exist_ok=True)
            tmp = os.path.join(cache_dir, f".{key}.tmp")
            with open(tmp, "wb") as fh:
                fh.write(base64.b64encode(audio))
            os.replace(tmp, os.path.join(cache_dir, key))
        except OSError:
            logger.debug("fishaudio cache write failed (non-fatal)", exc_info=True)

    def _synth_one(self, api_key, model, voice_id, text, fmt, speed) -> bytes:
        """One sentence -> audio bytes, with a 24h response cache and
        exponential-backoff retries on 429/5xx/network errors."""
        key = self._cache_key(text, voice_id, model, fmt, speed)
        if self._cache_enabled():
            cached = self._cache_read(key)
            if cached is not None:
                return base64.b64decode(cached)

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

        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                resp = requests.post(
                    _FISH_URL, headers=headers, json=body, timeout=60
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                continue

            if resp.status_code == 200:
                audio = resp.content
                if self._cache_enabled():
                    self._cache_write(key, audio)
                return audio

            status = resp.status_code
            snippet = resp.text[:200]
            if status == 401:
                raise RuntimeError(
                    "FISH_API_KEY rejected (HTTP 401; check key at "
                    "https://fish.audio/app/api-keys/)"
                )
            if status in (402, 403):
                raise RuntimeError(
                    f"Fish Audio quota/tier error {status}: your plan does not "
                    "cover this request. Check usage and tiers at "
                    "https://fish.audio/app/ (details: "
                    "https://docs.fish.audio/developer-guide/models-pricing/"
                    "models-overview)"
                )
            if status == 429:
                if attempt < _MAX_ATTEMPTS - 1:
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        delay = min(float(retry_after or ""), _RETRY_AFTER_CAP)
                    except (TypeError, ValueError):
                        delay = _BACKOFF_BASE * (2 ** attempt)
                    time.sleep(delay)
                    continue
                raise RuntimeError(
                    "Fish Audio rate limit hit (429) after retries. "
                    "Slow down or check your plan quota at "
                    "https://fish.audio/app/"
                )
            if status >= 500:
                last_exc = RuntimeError(
                    f"Fish Audio TTS server error {status}: {snippet}"
                )
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(_BACKOFF_BASE * (2 ** attempt))
                continue

            raise RuntimeError(f"Fish Audio TTS error {status}: {snippet}")

        if last_exc is not None:
            if isinstance(last_exc, requests.RequestException):
                raise RuntimeError(
                    f"Fish Audio request failed: {last_exc}"
                ) from last_exc
            raise last_exc
        raise RuntimeError("Fish Audio TTS: retries exhausted")

    # -- required interface -------------------------------------------------

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
