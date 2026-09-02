# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.0] - 2026-09-01

### Added
- `list_voices()`: voice catalog from `GET https://api.fish.audio/model`
  (paginated), mapped to the Hermes `TTSProvider` entry shape
  (`id`, `display`, `language`, `preview_url`). Cached in-process for 10
  minutes; returns `[]` on API failure instead of raising.
- `list_models()`: static, docs-sourced TTS model list
  (`s2.1-pro-free` default, plus `s2.1-pro`, `s2-pro`, `s1`).
- Response cache: synthesized audio is cached for 24 hours under the
  system temp dir (`TMPDIR`-aware), keyed on text + voice + model +
  format + speed. `FISH_TTS_CACHE=0` disables it; cache I/O failures
  are non-fatal.
- Retries with exponential backoff (2 retries) on 429/5xx/network
  errors, honoring `Retry-After` when present (capped at 30 s).
- Distinct, actionable error messages: 401 (bad key), 402/403
  (quota/tier), 429 (rate limit after retries), other non-200
  (status + response snippet).
- CI workflow (`.github/workflows/ci.yml`): pytest on push/PR, Python 3.11.
- `docs/plugin-index-entry.json`: draft entry for the Hermes community
  plugin index.

### Changed
- README: install section now leads with
  `hermes plugins install jd-chiles/hermes-plugin-fishaudio-tts` and the
  Settings voice-provider config flow; manual git clone is the
  alternative. Added CI/license/python badges.
- `plugin.yaml` version bumped to 1.1.0.

## [1.0.0] - 2026-09-01

### Added
- Initial release: `fishaudio` TTS provider for Hermes Agent.
  Sentence-chunked synthesis via `POST https://api.fish.audio/v1/tts`
  (`s2.1-pro-free` developer tier), speech normalization that strips
  fenced/inline code and markdown before synthesis, voice-bubble
  compatibility, and a dependency-free pytest suite.
