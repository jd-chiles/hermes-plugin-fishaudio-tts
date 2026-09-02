"""Fish Audio TTS plugin entry point.

Hermes loads this module and calls ``register(ctx)`` once. We register a
``fishaudio`` TTS provider so that ``tts.provider: fishaudio`` in
config.yaml routes every ``text_to_speech`` call to Fish Audio's API.

v1.2.0 additions:
  * A persisted settings layer (voice/model/latency/chunk_length) stored via
    ctx.get_config/set_config under ``plugins.entries.fishaudio.settings``.
    Resolution order at synth time: plugin setting > env var > default.
  * The ``fishaudio_voice_admin`` tool (list_voices / get_settings /
    set_settings / preview) for natural-language voice management — pair it
    with the bundled ``fishaudio:voice-admin`` skill.
  * The bundled skill itself, registered read-only via ctx.register_skill.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Singleton so the tool handler and future callers share one settings dict.
_PROVIDER: FishAudioTTSProvider | None = None


VOICE_ADMIN_SCHEMA = {
    "name": "fishaudio_voice_admin",
    "description": (
        "Manage Fish Audio TTS voices and settings. Actions: list_voices "
        "(browse the voice catalog), get_settings (current voice/model/"
        "latency/chunk_length), set_settings (persist voice_id/model/"
        "latency/chunk_length), preview (synthesize sample text and return "
        "base64 audio + metadata without persisting anything). "
        "For natural-language voice changes, first load the bundled skill "
        "fishaudio:voice-admin."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list_voices", "get_settings", "set_settings", "preview"],
                "description": "Which voice-admin operation to run.",
            },
            "settings": {
                "type": "object",
                "description": (
                    "For set_settings: keys voice_id (string or null to "
                    "unset), model, latency ('normal'|'balanced'), "
                    "chunk_length (int 20-2000)."
                ),
            },
            "text": {
                "type": "string",
                "description": "For preview: the text to synthesize.",
            },
            "voice_id": {
                "type": "string",
                "description": "For preview: optional per-request voice override.",
            },
            "model": {
                "type": "string",
                "description": "For preview: optional per-request model override.",
            },
        },
        "required": ["action"],
    },
}


def _err(message: str, **extra) -> str:
    return json.dumps({"ok": False, "error": message, **extra})


def _ok(payload: dict) -> str:
    return json.dumps({"ok": True, **payload})


def _handle_voice_admin(args: dict, **kwargs) -> str:
    """fishaudio_voice_admin tool handler. Returns a JSON string."""
    action = str(args.get("action") or "").strip().lower()
    provider = _PROVIDER
    if provider is None:  # pragma: no cover — register() always sets it
        return _err("voice-admin not initialized")

    if action == "list_voices":
        voices = provider.list_voices()
        models = provider.list_models()
        return _ok({
            "voices": voices,
            "voice_count": len(voices),
            "models": models,
        })

    if action == "get_settings":
        return _ok({"settings": provider.current_settings()})

    if action == "set_settings":
        from .provider import _validate_settings

        clean, errors = _validate_settings(args.get("settings"))
        if errors:
            return _err("; ".join(errors), errors=errors)
        # Validate voice_id against the live catalog when reachable.
        if clean.get("voice_id"):
            voices = provider.list_voices()
            if voices and clean["voice_id"] not in {v.get("id") for v in voices}:
                return _err(
                    f"voice_id {clean['voice_id']!r} not found in the "
                    "Fish Audio catalog; call list_voices first"
                )
        ctx = kwargs.get("ctx") or getattr(provider, "_settings_ctx", None)
        if ctx is None:  # pragma: no cover — register() always attaches it
            return _err("plugin context unavailable; cannot persist settings")
        try:
            for key, value in clean.items():
                ctx.set_config(key, value)
        except Exception as exc:
            return _err(f"failed to persist settings: {exc}")
        # Update the live dict only after a successful persist.
        provider.apply_settings(clean)
        return _ok({
            "settings": provider.current_settings(),
            "changed": sorted(clean),
        })

    if action == "preview":
        text = str(args.get("text") or "").strip()
        if not text:
            return _err("preview requires text")
        import os
        import tempfile

        if not os.environ.get("FISH_API_KEY"):
            return _err("FISH_API_KEY is not set; preview unavailable")
        voice = args.get("voice_id") or None
        model = args.get("model") or None
        try:
            fd, out_path = tempfile.mkstemp(suffix=".mp3", prefix="fish-preview-")
            os.close(fd)
            try:
                provider.synthesize(text, out_path, voice=voice, model=model)
                with open(out_path, "rb") as fh:
                    audio_b64 = base64.b64encode(fh.read()).decode("ascii")
            finally:
                try:
                    os.unlink(out_path)
                except OSError:
                    pass
        except Exception as exc:
            return _err(f"preview synthesis failed: {exc}")
        _, effective_model = provider._resolve(voice, model)
        return _ok({
            "audio_base64": audio_b64,
            "format": "mp3",
            "voice_id": voice or provider.settings.get("voice_id"),
            "model": effective_model,
            "text": text,
        })

    return _err(
        f"unknown action {action!r}; use list_voices, get_settings, "
        "set_settings, or preview"
    )


def register(ctx) -> None:
    """Register the Fish Audio TTS provider, settings, tool, and skill.

    Called once by the plugin loader.
    """
    global _PROVIDER
    from .provider import FishAudioTTSProvider

    provider = FishAudioTTSProvider()
    _PROVIDER = provider

    # Settings layer: snapshot persisted values at register() time into the
    # provider's mutable dict (synth-time resolution never needs ctx), and
    # keep the ctx handle so set_settings can persist later.
    provider.attach_settings(ctx)
    provider._settings_ctx = ctx

    ctx.register_tts_provider(provider)

    try:
        ctx.register_tool(
            name="fishaudio_voice_admin",
            toolset="fishaudio",
            schema=VOICE_ADMIN_SCHEMA,
            handler=_handle_voice_admin,
            description=VOICE_ADMIN_SCHEMA["description"],
        )
    except Exception as exc:
        logger.warning("fishaudio: tool registration failed: %s", exc)

    skill_path = Path(__file__).parent / "skills" / "voice-admin" / "SKILL.md"
    if skill_path.exists():
        try:
            ctx.register_skill("voice-admin", skill_path)
        except Exception as exc:
            logger.warning("fishaudio: skill registration failed: %s", exc)
