"""v1.2.0 tests — settings layer, fishaudio_voice_admin tool, plugin_api backend.

All HTTP is mocked (matching the rest of the suite); settings persistence is
exercised through a fake ctx recording set_config calls, never a real
config.yaml. Async endpoint handlers run via asyncio.run (no pytest-asyncio
dependency in this suite).
"""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

from .conftest import provider_mod, normalize_mod  # noqa: F401  (package init)

validate_settings = provider_mod._validate_settings


# ── settings validation ─────────────────────────────────────────────────────


class TestValidateSettings:
    def test_valid_full_patch(self):
        clean, errors = validate_settings({
            "voice_id": "abc-123",
            "model": "s2.1-pro",
            "latency": "normal",
            "chunk_length": 200,
        })
        assert errors == []
        assert clean == {
            "voice_id": "abc-123",
            "model": "s2.1-pro",
            "latency": "normal",
            "chunk_length": 200,
        }

    def test_voice_id_none_unsets(self):
        clean, errors = validate_settings({"voice_id": None})
        assert errors == []
        assert clean == {"voice_id": None}

    def test_voice_id_blank_string_unsets(self):
        clean, errors = validate_settings({"voice_id": "   "})
        assert errors == []
        assert clean == {"voice_id": None}

    def test_voice_id_wrong_type(self):
        clean, errors = validate_settings({"voice_id": 42})
        assert errors and "voice_id" in errors[0]

    def test_latency_rejects_unknown(self):
        clean, errors = validate_settings({"latency": "turbo"})
        assert errors and "latency" in errors[0]
        assert "latency" not in clean

    def test_chunk_length_range(self):
        _, low = validate_settings({"chunk_length": 5})
        _, high = validate_settings({"chunk_length": 99999})
        _, ok = validate_settings({"chunk_length": 120})
        assert low and high
        assert not ok

    def test_chunk_length_string_coerced(self):
        clean, errors = validate_settings({"chunk_length": "80"})
        assert errors == []
        assert clean["chunk_length"] == 80

    def test_chunk_length_garbage(self):
        _, errors = validate_settings({"chunk_length": "loud"})
        assert errors

    def test_unknown_keys_rejected(self):
        clean, errors = validate_settings({"api_key": "sk-leak", "model": "s1"})
        assert "api_key" not in clean
        assert any("api_key" in e for e in errors)

    def test_non_dict(self):
        clean, errors = validate_settings(["voice_id"])
        assert errors == ["settings patch must be an object"]


# ── precedence: setting > env > default ─────────────────────────────────────


class TestResolutionPrecedence:
    def test_env_only(self, provider, clean_env):
        clean_env.setenv("FISH_VOICE_ID", "env-voice")
        clean_env.setenv("FISH_TTS_MODEL", "s1")
        assert provider._resolve(None, None) == ("env-voice", "s1")

    def test_default_when_nothing_set(self, provider, clean_env):
        assert provider._resolve(None, None) == (None, provider_mod._DEFAULT_MODEL)

    def test_setting_beats_env(self, provider, clean_env):
        clean_env.setenv("FISH_VOICE_ID", "env-voice")
        clean_env.setenv("FISH_TTS_MODEL", "s1")
        provider.apply_settings({"voice_id": "cfg-voice", "model": "s2-pro"})
        assert provider._resolve(None, None) == ("cfg-voice", "s2-pro")

    def test_call_arg_beats_setting_and_env(self, provider, clean_env):
        clean_env.setenv("FISH_VOICE_ID", "env-voice")
        provider.apply_settings({"voice_id": "cfg-voice", "model": "s2-pro"})
        assert provider._resolve("call-voice", "s1") == ("call-voice", "s1")

    def test_latency_chunk_defaults(self, provider, clean_env):
        assert provider._effective_latency() == "balanced"
        assert provider._effective_chunk_length() == 120

    def test_latency_chunk_settings_override(self, provider, clean_env):
        provider.apply_settings({"latency": "normal", "chunk_length": 60})
        assert provider._effective_latency() == "normal"
        assert provider._effective_chunk_length() == 60

    def test_latency_chunk_reach_synth_body(self, provider, clean_env, monkeypatch):
        """latency/chunk_length settings must flow into the TTS request body."""
        clean_env.setenv("FISH_API_KEY", "test-key")
        provider.apply_settings({"latency": "normal", "chunk_length": 55})
        captured = {}

        class FakeResp:
            status_code = 200
            content = b"audio"

        def fake_post(url, headers=None, json=None, timeout=None):
            captured.update(json or {})
            return FakeResp()

        monkeypatch.setattr(provider_mod.requests, "post", fake_post)
        provider._synth_one("test-key", "s2.1-pro-free", None, "Hi.", "mp3", None)
        assert captured["latency"] == "normal"
        assert captured["chunk_length"] == 55

    def test_is_available_still_env_keyed(self, provider, clean_env):
        clean_env.delenv("FISH_API_KEY", raising=False)
        assert provider.is_available() is False
        clean_env.setenv("FISH_API_KEY", "k")
        assert provider.is_available() is True

    def test_secrets_never_in_settings(self, provider, clean_env):
        """API keys are not a setting; validation rejects them outright."""
        clean, errors = validate_settings({"api_key": "sk-x", "FISH_API_KEY": "sk-x"})
        assert clean == {}
        assert len(errors) >= 1


# ── attach_settings / fake ctx ──────────────────────────────────────────────


class FakeCtx:
    """Records set_config writes; get_config reads from the same store."""

    def __init__(self, store=None):
        self.store = dict(store or {})
        self.set_calls = []

    def get_config(self, key, default=None):
        return self.store.get(key, default)

    def set_config(self, key, value):
        self.set_calls.append((key, value))
        self.store[key] = value


class TestAttachSettings:
    def test_attach_reads_config(self, provider):
        ctx = FakeCtx({"voice_id": "cfg-v", "latency": "normal"})
        provider.attach_settings(ctx)
        assert provider.settings["voice_id"] == "cfg-v"
        assert provider.settings["latency"] == "normal"
        assert provider.settings["model"] is None

    def test_attach_tolerant_of_raising_get_config(self, provider):
        class RaisingCtx:
            def get_config(self, key, default=None):
                raise RuntimeError("no ctx here")

        provider.attach_settings(RaisingCtx())
        # Falls back to None per key, never raises.
        assert provider.settings["voice_id"] is None

    def test_missing_ctx_path_synth_works(self, provider, clean_env):
        """Settings live on the instance, so synth works with zero ctx."""
        provider.apply_settings({"model": "s1"})
        assert provider._resolve(None, None) == (None, "s1")


# ── fishaudio_voice_admin tool ──────────────────────────────────────────────


@pytest.fixture
def plugin_module():
    """Fresh plugin module with a fresh provider singleton."""
    import importlib
    name = "fishaudio_tts"
    return importlib.import_module(name)


@pytest.fixture
def wired(plugin_module):
    """Plugin registered against a FakeCtx — mirrors register()."""
    ctx = FakeCtx()
    provider = plugin_module.FishAudioTTSProvider()
    plugin_module._PROVIDER = provider
    provider.attach_settings(ctx)
    provider._settings_ctx = ctx
    handler = plugin_module._handle_voice_admin
    return handler, provider, ctx


class TestVoiceAdminTool:
    def test_returns_json_string(self, wired):
        handler, _, _ = wired
        out = handler({"action": "get_settings"})
        assert isinstance(out, str)
        data = json.loads(out)
        assert data["ok"] is True
        assert "voice_id" in data["settings"]

    def test_handler_accepts_kwargs(self, wired):
        handler, _, _ = wired
        # Hermes may pass extra kwargs — signature must tolerate them.
        out = handler({"action": "get_settings"}, task_id="x", other=1)
        assert json.loads(out)["ok"] is True

    def test_list_voices_mocked(self, wired, monkeypatch):
        handler, provider, _ = wired
        monkeypatch.setattr(
            provider, "list_voices",
            lambda: [{"id": "v1", "display": "Calm Narrator"}],
        )
        out = handler({"action": "list_voices"})
        data = json.loads(out)
        assert data["ok"] is True
        assert data["voice_count"] == 1
        assert data["voices"][0]["id"] == "v1"
        assert data["models"][0]["id"] == provider_mod._DEFAULT_MODEL

    def test_set_settings_persists_via_ctx(self, wired, monkeypatch):
        handler, provider, ctx = wired
        monkeypatch.setattr(
            provider, "list_voices",
            lambda: [{"id": "v1"}],
        )
        out = handler({"action": "set_settings", "settings": {"voice_id": "v1"}})
        data = json.loads(out)
        assert data["ok"] is True
        assert ("voice_id", "v1") in ctx.set_calls
        assert provider.settings["voice_id"] == "v1"

    def test_set_settings_validates_voice_against_catalog(self, wired, monkeypatch):
        handler, provider, ctx = wired
        monkeypatch.setattr(provider, "list_voices", lambda: [{"id": "v1"}])
        out = handler({
            "action": "set_settings",
            "settings": {"voice_id": "not-in-catalog"},
        })
        data = json.loads(out)
        assert data["ok"] is False
        assert "not found" in data["error"]
        assert ctx.set_calls == []  # nothing persisted
        assert provider.settings["voice_id"] is None

    def test_set_settings_skips_catalog_check_when_unreachable(self, wired, monkeypatch):
        handler, provider, ctx = wired
        monkeypatch.setattr(provider, "list_voices", lambda: [])  # API down
        out = handler({"action": "set_settings", "settings": {"voice_id": "any"}})
        assert json.loads(out)["ok"] is True

    def test_set_settings_rejects_invalid(self, wired):
        handler, provider, ctx = wired
        out = handler({"action": "set_settings", "settings": {"latency": "warp"}})
        data = json.loads(out)
        assert data["ok"] is False
        assert ctx.set_calls == []

    def test_set_settings_survives_set_config_failure(self, wired, monkeypatch):
        """If ctx.set_config raises, the live dict must NOT be updated."""
        handler, provider, ctx = wired

        def boom(key, value):
            raise PermissionError("managed install")

        ctx.set_config = boom
        monkeypatch.setattr(provider, "list_voices", lambda: [])
        out = handler({"action": "set_settings", "settings": {"model": "s1"}})
        data = json.loads(out)
        assert data["ok"] is False
        assert provider.settings["model"] is None  # unchanged

    def test_get_settings(self, wired):
        handler, provider, _ = wired
        provider.apply_settings({"voice_id": "v9"})
        data = json.loads(handler({"action": "get_settings"}))
        assert data["settings"]["voice_id"] == "v9"

    def test_preview_mocked(self, wired, clean_env, monkeypatch):
        handler, provider, _ = wired
        clean_env.setenv("FISH_API_KEY", "test-key")
        monkeypatch.setattr(
            provider, "synthesize",
            lambda text, path, voice=None, model=None: open(path, "wb").write(b"MP3DATA"),
        )
        out = handler({"action": "preview", "text": "hello"})
        data = json.loads(out)
        assert data["ok"] is True
        assert base64.b64decode(data["audio_base64"]) == b"MP3DATA"
        assert data["format"] == "mp3"

    def test_preview_persists_nothing(self, wired, clean_env, monkeypatch):
        handler, provider, ctx = wired
        clean_env.setenv("FISH_API_KEY", "test-key")
        monkeypatch.setattr(
            provider, "synthesize",
            lambda text, path, voice=None, model=None: open(path, "wb").write(b"x"),
        )
        handler({"action": "preview", "text": "hello", "voice_id": "v-tmp"})
        assert ctx.set_calls == []

    def test_preview_requires_key(self, wired, clean_env):
        handler, _, _ = wired
        clean_env.delenv("FISH_API_KEY", raising=False)
        data = json.loads(handler({"action": "preview", "text": "hi"}))
        assert data["ok"] is False and "FISH_API_KEY" in data["error"]

    def test_preview_requires_text(self, wired):
        handler, _, _ = wired
        data = json.loads(handler({"action": "preview"}))
        assert data["ok"] is False

    def test_unknown_action(self, wired):
        handler, _, _ = wired
        data = json.loads(handler({"action": "destroy"}))
        assert data["ok"] is False


# ── register() wiring ───────────────────────────────────────────────────────


class TestRegister:
    def test_register_wires_provider_tool_settings_skill(self, plugin_module):
        calls = {"tts": None, "tools": [], "skills": []}

        class Ctx:
            def register_tts_provider(self, provider):
                calls["tts"] = provider

            def register_tool(self, **kwargs):
                calls["tools"].append(kwargs)

            def register_skill(self, name, path):
                calls["skills"].append((name, path))

            def get_config(self, key, default=None):
                return default

        ctx = Ctx()
        plugin_module.register(ctx)

        assert calls["tts"] is plugin_module._PROVIDER
        assert len(calls["tools"]) == 1
        tool = calls["tools"][0]
        assert tool["name"] == "fishaudio_voice_admin"
        assert tool["schema"]["name"] == "fishaudio_voice_admin"
        # Tool description points the agent at the bundled skill.
        assert "fishaudio:voice-admin" in tool["description"]
        assert tool["schema"]["parameters"]["required"] == ["action"]
        assert calls["skills"] == [
            ("voice-admin", plugin_module.Path(
                plugin_module.__file__).parent / "skills" / "voice-admin" / "SKILL.md")
        ]
        # handler accepts **kwargs
        out = tool["handler"]({"action": "get_settings"}, extra=1)
        assert json.loads(out)["ok"] is True

    def test_register_survives_skill_registration_failure(self, plugin_module):
        class Ctx:
            def register_tts_provider(self, provider):
                pass

            def register_tool(self, **kwargs):
                pass

            def register_skill(self, name, path):
                raise RuntimeError("registry closed")

            def get_config(self, key, default=None):
                return default

        # Must not raise.
        plugin_module.register(Ctx())


# ── plugin_api.py backend (FastAPI router, handlers called directly) ────────


@pytest.fixture
def api():
    """The dashboard router module loaded fresh, with a wired provider."""
    import importlib.util
    import sys
    from pathlib import Path

    plugin_dir = Path(__file__).resolve().parent.parent
    name = "fishaudio_tts_api_test"
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, plugin_dir / "dashboard" / "plugin_api.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def run(coro):
    return asyncio.run(coro)


class TestPluginApi:
    def test_get_settings(self, api, plugin_module):
        provider = plugin_module.FishAudioTTSProvider()
        provider.apply_settings({"voice_id": "v1", "latency": "normal"})
        plugin_module._PROVIDER = provider
        resp = run(api.get_settings())
        assert resp["ok"] is True
        assert resp["settings"]["voice_id"] == "v1"

    def test_post_settings_validates(self, api, plugin_module, monkeypatch):
        provider = plugin_module.FishAudioTTSProvider()
        provider._settings_ctx = FakeCtx()
        plugin_module._PROVIDER = provider
        monkeypatch.setattr(provider, "list_voices", lambda: [])

        with pytest.raises(api.HTTPException) as exc:
            run(api.post_settings({"latency": "warp"}))
        assert exc.value.status_code == 400

        resp = run(api.post_settings({"latency": "normal", "chunk_length": 90}))
        assert resp["ok"] is True
        assert resp["changed"] == ["chunk_length", "latency"]
        assert resp["settings"]["latency"] == "normal"

    def test_post_settings_persists_via_ctx(self, api, plugin_module, monkeypatch):
        ctx = FakeCtx()
        provider = plugin_module.FishAudioTTSProvider()
        provider._settings_ctx = ctx
        plugin_module._PROVIDER = provider
        monkeypatch.setattr(provider, "list_voices", lambda: [])
        run(api.post_settings({"model": "s1"}))
        assert ("model", "s1") in ctx.set_calls
        assert provider.settings["model"] == "s1"

    def test_get_voices_mocked(self, api, plugin_module, monkeypatch):
        provider = plugin_module.FishAudioTTSProvider()
        plugin_module._PROVIDER = provider
        monkeypatch.setattr(
            provider, "list_voices",
            lambda: [{"id": "v1", "display": "Narrator"}],
        )
        api._voices_cache = None  # reset TTL cache
        resp = run(api.get_voices())
        assert resp["voice_count"] == 1
        assert resp["voices"][0]["display"] == "Narrator"

    def test_voices_ttl_cache(self, api, plugin_module, monkeypatch):
        provider = plugin_module.FishAudioTTSProvider()
        plugin_module._PROVIDER = provider
        count = {"n": 0}

        def counting_voices():
            count["n"] += 1
            return [{"id": f"v{count['n']}"}]

        monkeypatch.setattr(provider, "list_voices", counting_voices)
        api._voices_cache = None
        first = run(api.get_voices())
        second = run(api.get_voices())
        assert count["n"] == 1          # served from TTL cache
        assert first["voices"] == second["voices"]

    def test_preview_mocked(self, api, plugin_module, clean_env, monkeypatch):
        provider = plugin_module.FishAudioTTSProvider()
        plugin_module._PROVIDER = provider
        clean_env.setenv("FISH_API_KEY", "test-key")

        def fake_synth(text, path, voice=None, model=None):
            with open(path, "wb") as fh:
                fh.write(b"PREVIEW")

        monkeypatch.setattr(provider, "synthesize", fake_synth)
        resp = run(api.preview({"text": "test"}))
        assert resp["ok"] is True
        assert base64.b64decode(resp["audio_base64"]) == b"PREVIEW"

    def test_preview_requires_text(self, api, plugin_module):
        plugin_module._PROVIDER = plugin_module.FishAudioTTSProvider()
        with pytest.raises(api.HTTPException) as exc:
            run(api.preview({}))
        assert exc.value.status_code == 400

    def test_preview_requires_key(self, api, plugin_module, clean_env):
        plugin_module._PROVIDER = plugin_module.FishAudioTTSProvider()
        clean_env.delenv("FISH_API_KEY", raising=False)
        with pytest.raises(api.HTTPException) as exc:
            run(api.preview({"text": "hi"}))
        assert exc.value.status_code == 503

    def test_no_secrets_in_any_response(self, api, plugin_module, clean_env, monkeypatch):
        """Responses must never contain the API key value."""
        provider = plugin_module.FishAudioTTSProvider()
        provider._settings_ctx = FakeCtx()
        plugin_module._PROVIDER = provider
        secret = "sk-test-secret-123"
        clean_env.setenv("FISH_API_KEY", secret)
        monkeypatch.setattr(provider, "list_voices", lambda: [])

        def check(obj):
            text = json.dumps(obj, default=str)
            assert secret not in text

        check(run(api.get_settings()))
        check(run(api.post_settings({"latency": "normal"})))
        check(run(api.get_voices()))

    def test_router_exists(self, api):
        # The web server mounts `router` — it must be present.
        assert hasattr(api, "router")
