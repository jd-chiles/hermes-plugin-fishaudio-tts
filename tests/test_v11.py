"""v1.1.0 behavior tests: voice/model catalog, retries, error taxonomy,
response cache — all against mocked HTTP. No Hermes, no network."""
from __future__ import annotations

import base64
import importlib
import json
import time

import pytest
import requests as requests_lib

import fishaudio_tts.provider as provider_mod  # noqa: F401  (conftest preloads pkg)

FishAudioTTSProvider = provider_mod.FishAudioTTSProvider


class _FakeResponse:
    def __init__(self, status_code=200, content=b"AUDIO", text="", headers=None,
                 payload=None):
        self.status_code = status_code
        self.content = content
        if payload is not None:
            text = json.dumps(payload)
        self.text = text
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


class _PostRecorder:
    """Captures requests.post calls; returns queued responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)


class _GetRecorder:
    """Captures requests.get calls; returns queued responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        return self.responses.pop(0)


def _voice_page(ids, has_more=False):
    return _FakeResponse(payload={
        "total": len(ids),
        "items": [
            {"_id": i, "title": f"Voice {i}", "languages": ["en"],
             "samples": [{"filename": "sample.mp3"}]}
            for i in ids
        ],
        "has_more": has_more,
    })


@pytest.fixture
def fresh(monkeypatch, clean_env, tmp_path):
    """Provider with a cold in-process cache, clean env, isolated cache dir."""
    clean_env.setenv("FISH_API_KEY", "test-key")
    clean_env.setenv("TMPDIR", str(tmp_path))
    p = provider_mod.FishAudioTTSProvider()
    return p, clean_env


class TestListModels:
    def test_static_docs_list(self, fresh):
        p, _ = fresh
        models = p.list_models()
        ids = [m["id"] for m in models]
        assert ids[0] == "s2.1-pro-free"
        assert "s2.1-pro" in ids and "s2-pro" in ids
        assert all("display" in m for m in models)

    def test_returns_copies(self, fresh):
        p, _ = fresh
        m1, m2 = p.list_models(), p.list_models()
        m1[0]["id"] = "mutated"
        assert m2[0]["id"] == "s2.1-pro-free"


class TestListVoices:
    def test_maps_catalog_shape(self, fresh, monkeypatch):
        p, _ = fresh
        rec = _GetRecorder([_voice_page(["abc123"], has_more=False)])
        monkeypatch.setattr(provider_mod.requests, "get", rec)
        voices = p.list_voices()
        assert voices[0]["id"] == "abc123"
        assert voices[0]["display"] == "Voice abc123"
        assert voices[0]["language"] == "en"
        assert "preview_url" in voices[0]
        call = rec.calls[0]
        assert call["url"] == "https://api.fish.audio/model"
        assert call["headers"]["Authorization"] == "Bearer test-key"
        assert call["params"]["page_size"] == 100
        assert call["params"]["page_number"] == 1

    def test_follows_pagination(self, fresh, monkeypatch):
        p, _ = fresh
        rec = _GetRecorder([
            _voice_page(["a"], has_more=True),
            _voice_page(["b"], has_more=False),
        ])
        monkeypatch.setattr(provider_mod.requests, "get", rec)
        voices = p.list_voices()
        assert [v["id"] for v in voices] == ["a", "b"]
        assert rec.calls[1]["params"]["page_number"] == 2

    def test_cached_second_call_no_http(self, fresh, monkeypatch):
        p, _ = fresh
        rec = _GetRecorder([_voice_page(["abc123"])])
        monkeypatch.setattr(provider_mod.requests, "get", rec)
        p.list_voices()
        p.list_voices()
        assert len(rec.calls) == 1

    def test_cache_expires_after_ttl(self, fresh, monkeypatch):
        p, _ = fresh
        rec = _GetRecorder([_voice_page(["abc123"]), _voice_page(["def456"])])
        monkeypatch.setattr(provider_mod.requests, "get", rec)
        p.list_voices()
        p._voices_cached_at -= provider_mod._VOICES_TTL + 1
        voices = p.list_voices()
        assert len(rec.calls) == 2
        assert voices[0]["id"] == "def456"

    def test_api_failure_returns_empty_list(self, fresh, monkeypatch):
        p, _ = fresh
        rec = _GetRecorder([_FakeResponse(status_code=500, text="boom")])
        monkeypatch.setattr(provider_mod.requests, "get", rec)
        assert p.list_voices() == []

    def test_network_failure_returns_empty_list(self, fresh, monkeypatch):
        p, _ = fresh

        def boom(*a, **kw):
            raise requests_lib.ConnectionError("down")

        monkeypatch.setattr(provider_mod.requests, "get", boom)
        assert p.list_voices() == []

    def test_no_key_returns_empty_list(self, fresh, clean_env):
        p, _ = fresh
        clean_env.delenv("FISH_API_KEY", raising=False)
        assert p.list_voices() == []


class TestRetries:
    def test_429_retries_then_succeeds(self, fresh, monkeypatch):
        p, _ = fresh
        rec = _PostRecorder([
            _FakeResponse(status_code=429, text="slow down",
                          headers={"Retry-After": "0"}),
            _FakeResponse(status_code=200, content=b"OK"),
        ])
        monkeypatch.setattr(provider_mod.requests, "post", rec)
        monkeypatch.setattr(provider_mod.time, "sleep", lambda s: None)
        out = p.synthesize("Hi.", "/tmp/out.mp3")
        assert len(rec.calls) == 2
        assert out == "/tmp/out.mp3"

    def test_429_exhausted_raises_rate_limit_message(self, fresh, monkeypatch):
        p, _ = fresh
        rec = _PostRecorder([
            _FakeResponse(status_code=429, text="x", headers={"Retry-After": "0"}),
            _FakeResponse(status_code=429, text="x", headers={"Retry-After": "0"}),
            _FakeResponse(status_code=429, text="x", headers={"Retry-After": "0"}),
        ])
        monkeypatch.setattr(provider_mod.requests, "post", rec)
        monkeypatch.setattr(provider_mod.time, "sleep", lambda s: None)
        with pytest.raises(RuntimeError, match="rate limit"):
            p.synthesize("Hi.", "/tmp/out.mp3")

    def test_429_honors_retry_after_header(self, fresh, monkeypatch):
        p, _ = fresh
        rec = _PostRecorder([
            _FakeResponse(status_code=429, headers={"Retry-After": "7"}),
            _FakeResponse(status_code=200),
        ])
        monkeypatch.setattr(provider_mod.requests, "post", rec)
        slept = []
        monkeypatch.setattr(provider_mod.time, "sleep", slept.append)
        p.synthesize("Hi.", "/tmp/out.mp3")
        assert slept == [7.0]

    def test_5xx_retries_with_backoff(self, fresh, monkeypatch):
        p, _ = fresh
        rec = _PostRecorder([
            _FakeResponse(status_code=503, text="overloaded"),
            _FakeResponse(status_code=500, text="still"),
            _FakeResponse(status_code=200),
        ])
        monkeypatch.setattr(provider_mod.requests, "post", rec)
        monkeypatch.setattr(provider_mod.time, "sleep", lambda s: None)
        p.synthesize("Hi.", "/tmp/out.mp3")
        assert len(rec.calls) == 3

    def test_network_error_retries_then_raises(self, fresh, monkeypatch):
        p, _ = fresh
        attempts = []

        def flaky(*a, **kw):
            attempts.append(1)
            raise requests_lib.ConnectionError("down")

        monkeypatch.setattr(provider_mod.requests, "post", flaky)
        monkeypatch.setattr(provider_mod.time, "sleep", lambda s: None)
        with pytest.raises(RuntimeError, match="Fish Audio request failed"):
            p.synthesize("Hi.", "/tmp/out.mp3")
        assert len(attempts) == 3


class TestErrorTaxonomy:
    def test_401_actionable_message(self, fresh, monkeypatch):
        p, _ = fresh
        monkeypatch.setattr(provider_mod.requests, "post",
                            _PostRecorder([_FakeResponse(status_code=401)]))
        with pytest.raises(RuntimeError,
                           match=r"FISH_API_KEY rejected.*api-keys"):
            p.synthesize("Hi.", "/tmp/out.mp3")

    @pytest.mark.parametrize("status", [402, 403])
    def test_quota_tier_message(self, fresh, monkeypatch, status):
        p, _ = fresh
        monkeypatch.setattr(provider_mod.requests, "post",
                            _PostRecorder([_FakeResponse(status_code=status)]))
        with pytest.raises(RuntimeError, match="quota/tier"):
            p.synthesize("Hi.", "/tmp/out.mp3")

    def test_other_status_includes_snippet(self, fresh, monkeypatch):
        p, _ = fresh
        monkeypatch.setattr(provider_mod.requests, "post",
                            _PostRecorder([_FakeResponse(
                                status_code=418, text="teapot detail")]))
        with pytest.raises(RuntimeError, match="418.*teapot detail"):
            p.synthesize("Hi.", "/tmp/out.mp3")


class TestResponseCache:
    def _two_providers(self, fresh, monkeypatch, content=b"AUDIO"):
        p, _ = fresh
        rec = _PostRecorder([_FakeResponse(content=content),
                             _FakeResponse(content=b"SHOULD-NOT-BE-USED")])
        monkeypatch.setattr(provider_mod.requests, "post", rec)
        return p, rec

    def test_hit_and_miss(self, fresh, monkeypatch, tmp_path):
        p, rec = self._two_providers(fresh, monkeypatch)
        p.synthesize("Hi.", str(tmp_path / "a.mp3"))
        p.synthesize("Hi.", str(tmp_path / "b.mp3"))   # same key -> cache hit
        assert len(rec.calls) == 1
        p.synthesize("Different text.", str(tmp_path / "c.mp3"))
        assert len(rec.calls) == 2

    def test_disable_via_env(self, fresh, monkeypatch, tmp_path):
        fresh[1].setenv("FISH_TTS_CACHE", "0")
        p, rec = self._two_providers(fresh, monkeypatch)
        p.synthesize("Hi.", str(tmp_path / "a.mp3"))
        p.synthesize("Hi.", str(tmp_path / "b.mp3"))
        assert len(rec.calls) == 2

    def test_key_covers_voice_model_format_speed(self, fresh, monkeypatch,
                                                 tmp_path):
        p, rec = self._two_providers(fresh, monkeypatch)
        p.synthesize("Hi.", str(tmp_path / "a.mp3"))
        p.synthesize("Hi.", str(tmp_path / "b.mp3"), speed=1.5)
        assert len(rec.calls) == 2

    def test_cache_entry_roundtrips_bytes(self, fresh, monkeypatch, tmp_path):
        p, _ = self._two_providers(fresh, monkeypatch, content=b"WAV-DATA")
        p.synthesize("Hi.", str(tmp_path / "a.mp3"), format="wav")
        key = p._cache_key("Hi.", None, "s2.1-pro-free", "wav", None)
        raw = p._cache_read(key)
        assert base64.b64decode(raw) == b"WAV-DATA"

    def test_expired_entry_is_miss(self, fresh, monkeypatch, tmp_path):
        p, rec = self._two_providers(fresh, monkeypatch)
        p.synthesize("Hi.", str(tmp_path / "a.mp3"))
        # Backdate every cache file past the TTL.
        for name in provider_mod.os.listdir(provider_mod._cache_dir()):
            path = provider_mod.os.path.join(provider_mod._cache_dir(), name)
            past = time.time() - provider_mod._CACHE_TTL - 10
            provider_mod.os.utime(path, (past, past))
        p.synthesize("Hi.", str(tmp_path / "b.mp3"))
        assert len(rec.calls) == 2

    def test_cache_respects_tmpdir(self, fresh, monkeypatch, tmp_path):
        assert provider_mod._cache_dir().startswith(str(tmp_path))
        rec = _PostRecorder([_FakeResponse(content=b"A")])
        monkeypatch.setattr(provider_mod.requests, "post", rec)
        fresh[0].synthesize("Hi.", str(tmp_path / "a.mp3"))
        assert any(
            provider_mod._cache_dir() == str(f.parent)
            for f in tmp_path.rglob("*") if f.is_file()
        )
