"""Fish Audio TTS plugin — dashboard/desktop backend API.

Mounted at /api/plugins/fishaudio/ by the Hermes dashboard (declared in
dashboard/manifest.json ``api``) and consumed by both the desktop half
(desktop/plugin.js via ctx.rest) and the web dashboard.

Endpoints:
  GET  /settings  — current plugin settings (voice/model/latency/chunk_length)
  POST /settings  — validate + persist settings (same rules as the tool)
  GET  /voices    — voice catalog, per-request fetch with a short TTL cache
  POST /preview   — text (+ optional voice/model) -> base64 mp3 + metadata

No secrets in any response — FISH_API_KEY never leaves .env.

The plugin (fishaudio) is imported lazily inside handlers so this module
loads cleanly even when the plugin package isn't importable in a test
harness (same defensive pattern as the achievements backend).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

try:
    from fastapi import APIRouter, HTTPException  # type: ignore[no-redef]
except Exception:  # Allows local unit tests without dashboard dependencies.
    class APIRouter:  # type: ignore[no-redef,misc]
        def get(self, *_args, **_kwargs):
            return lambda fn: fn

        def post(self, *_args, **_kwargs):
            return lambda fn: fn

    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int, detail: str = "") -> None:
            self.status_code = status_code
            self.detail = detail

log = logging.getLogger(__name__)

router = APIRouter()

_VOICES_TTL_SECONDS = 120.0
_voices_lock = threading.Lock()
_voices_cache: Optional[List[Dict[str, Any]]] = None
_voices_cached_at: float = 0.0


def _plugin():
    """Import the plugin package lazily; 503 when unavailable."""
    try:
        import fishaudio_tts as plugin  # type: ignore[import-not-found]
    except Exception:
        # Fall back to a direct load of the sibling package (the dashboard
        # imports this file standalone, so the plugin dir isn't always on
        # sys.path under the test package name).
        import importlib.util
        import sys
        from pathlib import Path

        plugin_dir = Path(__file__).resolve().parent.parent
        name = "fishaudio_tts_dash"
        mod = sys.modules.get(name)
        if mod is None:
            spec = importlib.util.spec_from_file_location(
                name, plugin_dir / "__init__.py",
                submodule_search_locations=[str(plugin_dir)],
            )
            if spec is None:
                raise HTTPException(status_code=503, detail="fishaudio plugin unavailable")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception as exc:
                sys.modules.pop(name, None)
                log.warning("fishaudio plugin import failed: %s", exc)
                raise HTTPException(status_code=503, detail="fishaudio plugin unavailable")
        plugin = mod
    return plugin


def _provider():
    plugin = _plugin()
    provider = getattr(plugin, "_PROVIDER", None)
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail="fishaudio provider not initialized (plugin not loaded yet)",
        )
    return provider


def _plugin_context():
    """A live PluginContext for this plugin, or None (dashboard-only process).

    Reuses the provider's attached ctx when the plugin was loaded in-process
    (the gateway case — the desired path). Imported lazily so the module
    still loads outside a Hermes install.
    """
    provider = getattr(_plugin(), "_PROVIDER", None)
    ctx = getattr(provider, "_settings_ctx", None) if provider else None
    if ctx is not None:
        return ctx
    try:
        from hermes_cli.plugins import PluginContext, get_plugin_manager

        pm = get_plugin_manager()
        loaded = getattr(pm, "_plugins", {}).get("tts/fishaudio")
        if loaded is not None:
            return PluginContext(loaded.manifest, pm)
    except Exception:
        return None
    return None


@router.get("/settings")
async def get_settings():
    provider = _provider()
    return {"ok": True, "settings": provider.current_settings()}


@router.post("/settings")
async def post_settings(body: Dict[str, Any]):
    """Validate + persist a settings patch (same rules as the tool)."""
    provider = _provider()
    from fishaudio_tts.provider import _validate_settings

    clean, errors = _validate_settings(body or {})
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
    if clean.get("voice_id"):
        voices = await get_voices_cached()
        if voices and clean["voice_id"] not in {v.get("id") for v in voices}:
            raise HTTPException(
                status_code=400,
                detail=f"voice_id {clean['voice_id']!r} not found in the Fish Audio catalog",
            )
    try:
        for key, value in clean.items():
            ctx = _plugin_context()
            if ctx is None:
                # No live PluginContext in this process (e.g. dashboard-only
                # process): fall back to the direct config write path used by
                # hermes_cli itself — same file, same atomic-write mechanics.
                from hermes_cli.config import load_config, save_config

                config = load_config()
                entry = (
                    config.setdefault("plugins", {})
                    .setdefault("entries", {})
                    .setdefault("tts/fishaudio", {})
                    .setdefault("settings", {})
                )
                entry[key] = value
                save_config(config)
            else:
                ctx.set_config(key, value)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to persist settings: {exc}")
    # Update the live settings dict only after a successful persist.
    provider.apply_settings(clean)
    return {"ok": True, "settings": provider.current_settings(), "changed": sorted(clean)}


async def get_voices_cached() -> List[Dict[str, Any]]:
    """Voice catalog with a short in-process TTL cache (120s)."""
    global _voices_cache, _voices_cached_at
    provider = _provider()
    now = time.monotonic()
    with _voices_lock:
        if (
            _voices_cache is not None
            and now - _voices_cached_at < _VOICES_TTL_SECONDS
        ):
            return _voices_cache
    voices = provider.list_voices()
    with _voices_lock:
        _voices_cache = voices
        _voices_cached_at = now
    return voices


@router.get("/voices")
async def get_voices():
    provider = _provider()
    voices = await get_voices_cached()
    return {
        "ok": True,
        "voices": voices,
        "voice_count": len(voices),
        "models": provider.list_models(),
    }


@router.post("/preview")
async def preview(body: Dict[str, Any]):
    """Synthesize sample text and return base64 mp3 + metadata.

    Persists nothing — any voice/model in the body applies to this request
    only. Requires FISH_API_KEY in the gateway process environment.
    """
    import base64
    import os
    import tempfile

    provider = _provider()
    text = str((body or {}).get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if not os.environ.get("FISH_API_KEY"):
        raise HTTPException(status_code=503, detail="FISH_API_KEY is not set")
    voice = body.get("voice_id") or None
    model = body.get("model") or None
    import asyncio

    fd, out_path = tempfile.mkstemp(suffix=".mp3", prefix="fish-preview-")
    os.close(fd)
    try:
        # Synthesis is blocking network I/O — keep it off the event loop.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: provider.synthesize(text, out_path, voice=voice, model=model),
        )
        with open(out_path, "rb") as fh:
            audio_b64 = base64.b64encode(fh.read()).decode("ascii")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"preview synthesis failed: {exc}")
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    _, effective_model = provider._resolve(voice, model)
    return {
        "ok": True,
        "audio_base64": audio_b64,
        "format": "mp3",
        "voice_id": voice or provider.settings.get("voice_id"),
        "model": effective_model,
        "text": text,
    }
