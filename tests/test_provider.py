"""Unit tests for FishAudioTTSProvider: config resolution, request payload
shape, and availability — all against mocked HTTP. No Hermes, no network."""
from __future__ import annotations

import pytest

import fishaudio_tts.provider as provider_mod  # noqa: F401  (conftest preloads pkg)

FishAudioTTSProvider = provider_mod.FishAudioTTSProvider


class _FakeResponse:
    def __init__(self, status_code=200, content=b"AUDIO", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


class _Recorder:
    """Captures requests.post calls; returns canned audio bytes."""

    def __init__(self, response=None):
        self.calls = []
        self.response = response or _FakeResponse()

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json,
                           "timeout": timeout})
        return self.response


@pytest.fixture
def synth(monkeypatch, clean_env):
    """Provider with requests.post recorded. Returns (provider, recorder)."""
    rec = _Recorder()
    monkeypatch.setattr(provider_mod.requests, "post", rec)
    p = FishAudioTTSProvider()
    return p, rec


class TestBasics:
    def test_name_and_display(self, provider):
        assert provider.name == "fishaudio"
        assert provider.display_name == "Fish Audio"

    def test_voice_compatible_opt_in(self, provider):
        assert provider.voice_compatible is True


class TestAvailability:
    def test_unavailable_without_key(self, synth):
        p, _ = synth
        assert p.is_available() is False

    def test_available_with_key(self, synth, clean_env):
        clean_env.setenv("FISH_API_KEY", "test-key")
        p, _ = synth
        assert p.is_available() is True

    def test_synthesize_raises_without_key(self, synth):
        p, rec = synth
        with pytest.raises(RuntimeError, match="FISH_API_KEY"):
            p.synthesize("hello", "/tmp/out.mp3")
        assert rec.calls == []


class TestConfigResolution:
    def test_env_precedence_over_defaults(self, synth, clean_env):
        clean_env.setenv("FISH_API_KEY", "k")
        clean_env.setenv("FISH_VOICE_ID", "env-voice")
        clean_env.setenv("FISH_TTS_MODEL", "env-model")
        p, _ = synth
        assert p._resolve(None, None) == ("env-voice", "env-model")

    def test_explicit_args_override_env(self, synth, clean_env):
        clean_env.setenv("FISH_API_KEY", "k")
        clean_env.setenv("FISH_VOICE_ID", "env-voice")
        clean_env.setenv("FISH_TTS_MODEL", "env-model")
        p, _ = synth
        assert p._resolve("arg-voice", "arg-model") == ("arg-voice", "arg-model")

    def test_defaults_when_unset(self, synth):
        p, _ = synth
        voice, model = p._resolve(None, None)
        assert voice is None
        assert model == "s2.1-pro-free"


class TestRequestPayload:
    def test_payload_shape_and_headers(self, synth, clean_env, tmp_path):
        clean_env.setenv("FISH_API_KEY", "test-key")
        p, rec = synth
        out = tmp_path / "out.mp3"
        p.synthesize("One sentence.", str(out))

        assert len(rec.calls) == 1
        call = rec.calls[0]
        assert call["url"] == "https://api.fish.audio/v1/tts"
        assert call["headers"]["Authorization"] == "Bearer test-key"
        assert call["headers"]["Content-Type"] == "application/json"
        body = call["json"]
        assert body["text"] == "One sentence."
        assert body["format"] == "mp3"
        assert body["latency"] == "balanced"
        assert body["chunk_length"] == 120
        assert "reference_id" not in body  # no voice configured

    def test_voice_id_sent_as_reference_id(self, synth, clean_env, tmp_path):
        clean_env.setenv("FISH_API_KEY", "k")
        clean_env.setenv("FISH_VOICE_ID", "voice-123")
        p, rec = synth
        p.synthesize("Hi.", str(tmp_path / "o.mp3"))
        assert rec.calls[0]["json"]["reference_id"] == "voice-123"

    def test_model_header_uses_env_model(self, synth, clean_env, tmp_path):
        clean_env.setenv("FISH_API_KEY", "k")
        clean_env.setenv("FISH_TTS_MODEL", "s2.1-pro")
        p, rec = synth
        p.synthesize("Hi.", str(tmp_path / "o.mp3"))
        assert rec.calls[0]["headers"]["model"] == "s2.1-pro"

    def test_speed_becomes_prosody(self, synth, clean_env, tmp_path):
        clean_env.setenv("FISH_API_KEY", "k")
        p, rec = synth
        p.synthesize("Hi.", str(tmp_path / "o.mp3"), speed=1.5)
        assert rec.calls[0]["json"]["prosody"] == {"speed": 1.5}

    def test_no_prosody_when_speed_unset(self, synth, clean_env, tmp_path):
        clean_env.setenv("FISH_API_KEY", "k")
        p, rec = synth
        p.synthesize("Hi.", str(tmp_path / "o.mp3"))
        assert "prosody" not in rec.calls[0]["json"]

    def test_output_file_contains_audio_bytes(self, synth, clean_env, tmp_path):
        clean_env.setenv("FISH_API_KEY", "k")
        p, _ = synth
        out = tmp_path / "o.mp3"
        result = p.synthesize("Hi.", str(out))
        assert result == str(out)
        assert out.read_bytes() == b"AUDIO"


class TestSentenceChunking:
    def test_one_request_per_sentence(self, synth, clean_env, tmp_path):
        clean_env.setenv("FISH_API_KEY", "k")
        p, rec = synth
        p.synthesize("First here. Second one! Third one? Done.", str(tmp_path / "o.mp3"))
        assert len(rec.calls) == 4
        assert [c["json"]["text"] for c in rec.calls] == [
            "First here.", "Second one!", "Third one?", "Done.",
        ]

    def test_code_marker_becomes_its_own_sentence(self, synth, clean_env, tmp_path):
        clean_env.setenv("FISH_API_KEY", "k")
        p, rec = synth
        p.synthesize("Here:\n```py\nx=1\n```", str(tmp_path / "o.mp3"))
        texts = [c["json"]["text"] for c in rec.calls]
        assert any("dropped the code" in t for t in texts)
        assert all("x=1" not in t for t in texts)

    def test_empty_speech_falls_back_to_filler(self, synth, clean_env, tmp_path):
        clean_env.setenv("FISH_API_KEY", "k")
        p, rec = synth
        p.synthesize("", str(tmp_path / "o.mp3"))
        assert rec.calls[0]["json"]["text"] == "Okay."

    def test_code_only_reply_speaks_marker_not_filler(self, synth, clean_env, tmp_path):
        """A reply that is ONLY code still speaks the marker (never empty)."""
        clean_env.setenv("FISH_API_KEY", "k")
        p, rec = synth
        p.synthesize("```\nonly code\n```", str(tmp_path / "o.mp3"))
        assert rec.calls[0]["json"]["text"] != ""
        assert "Okay." != rec.calls[0]["json"]["text"]


class TestErrorHandling:
    def test_http_error_raises(self, monkeypatch, clean_env, tmp_path):
        clean_env.setenv("FISH_API_KEY", "k")
        monkeypatch.setattr(
            provider_mod.requests, "post",
            _Recorder(_FakeResponse(status_code=401, text="bad key")),
        )
        p = FishAudioTTSProvider()
        with pytest.raises(RuntimeError, match="401"):
            p.synthesize("Hi.", str(tmp_path / "o.mp3"))

    def test_network_error_wrapped(self, monkeypatch, clean_env, tmp_path):
        import requests as requests_lib
        clean_env.setenv("FISH_API_KEY", "k")

        def boom(*a, **kw):
            raise requests_lib.ConnectionError("down")

        monkeypatch.setattr(provider_mod.requests, "post", boom)
        p = FishAudioTTSProvider()
        with pytest.raises(RuntimeError, match="Fish Audio request failed"):
            p.synthesize("Hi.", str(tmp_path / "o.mp3"))


class TestSetupSchema:
    def test_schema_lists_required_env_vars(self, provider):
        schema = provider.get_setup_schema()
        keys = [v["key"] for v in schema["env_vars"]]
        assert keys == ["FISH_API_KEY", "FISH_VOICE_ID", "FISH_TTS_MODEL"]
