# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.2.0] - 2026-09-02

### Added
- **Persisted settings layer**: voice/model/latency/chunk_length stored via
  `ctx.set_config` under `plugins.entries.fishaudio.settings` in config.yaml.
  Resolution order at synth time: per-call override > plugin setting >
  env var > built-in default (`balanced` latency, `chunk_length` 120,
  model `s2.1-pro-free`). Settings are snapshotted at register() time into
  the provider, so the live synth path never depends on ctx being reachable;
  `set_settings` updates the snapshot only after a successful persist.
  Secrets are excluded by design — `FISH_API_KEY` stays in `.env` only.
- **`fishaudio_voice_admin` tool** (one tool, `action` param):
  `list_voices` (catalog + models), `get_settings`, `set_settings`
  (validated; voice_id checked against the live catalog when reachable),
  `preview` (text → base64 mp3 + metadata, persists nothing). Returns JSON
  strings; handler accepts `**kwargs`. The tool description directs the
  agent to load the bundled skill first.
- **Bundled skill `fishaudio:voice-admin`** (`skills/voice-admin/SKILL.md`,
  registered read-only via `ctx.register_skill`): the natural-language
  voice-change workflow — list voices, propose matches, confirm, set,
  offer preview; never echo `FISH_API_KEY`; never invent voice ids.
- **Dashboard backend** `dashboard/plugin_api.py` (manifest-declared,
  mounted at `/api/plugins/fishaudio/`): `GET /settings`,
  `POST /settings` (same validation as the tool), `GET /voices`
  (per-request fetch + 120 s TTL cache), `POST /preview` (base64 mp3 via
  executor, so blocking synthesis stays off the event loop). No secrets in
  any response.
- **Dashboard Voice Studio** (`dashboard/dist/`): `/fish-audio` tab with a
  searchable voice picker, model Select, latency SegmentedControl,
  chunk_length Input, Preview with in-browser audio playback (blob URL),
  Save/Revert, and current persisted values shown on load. UI-kit
  components + theme variables only.
- **Desktop half** `desktop/plugin.js`: the same Voice Studio panel for the
  native desktop app (unified-package door, `$HERMES_HOME/plugins/<id>/
  desktop/plugin.js`). Opt-in — inventories in Settings → Plugins, off
  until the user toggles it, then ⌘K → Reload desktop plugins.

### Changed
- `plugin.yaml` bumped to 1.2.0 with v2 manifest fields: `manifest_version: 2`,
  `api_version: 1`, `config_schema` for the four settings,
  `provides_tools`, `requires_env`, `license`, `homepage`, `tags`.
- `_SYNTH_KWARGS` static spread replaced by per-request resolution of
  `latency`/`chunk_length` from the settings layer (payload shape unchanged).
- README: settings-layer precedence docs, voice-management section
  (tool + skill + studio), updated file map, desktop opt-in note.

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
