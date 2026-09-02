---
name: voice-admin
description: "Change the user's Fish Audio text-to-speech voice or settings from a natural-language description. Load when the user asks to switch voices, pick a voice that matches a persona, or tune TTS model/latency/chunk_length."
version: 1.2.0
---

# Fish Audio voice-admin workflow

You manage the Fish Audio TTS settings for this Hermes instance through the
`fishaudio_voice_admin` tool. Follow this exact flow.

## Workflow

1. **List voices** — call `fishaudio_voice_admin` with
   `{"action": "list_voices"}`. The result includes the voice catalog
   (`id`, `display`, `language`, optional `preview_url`) and the available
   TTS models. If the catalog comes back empty, say the catalog is
   unavailable and offer to set a voice by id instead.

2. **Propose a match** — from the user's description ("a calm female
   narrator", "an energetic male voice", …), pick the 2-3 best catalog
   entries (match on `display` text and `language`) and present them with
   their ids. Never invent ids — only propose ids from the catalog.

3. **Confirm** — ask the user which one to apply before changing anything.

4. **Apply** — call `fishaudio_voice_admin` with
   `{"action": "set_settings", "settings": {"voice_id": "<id>"}}`.
   The tool validates the id against the catalog and persists it under the
   plugin's settings (config.yaml → `plugins.entries.fishaudio.settings`),
   so the choice survives restarts and takes precedence over the
   `FISH_VOICE_ID` environment variable. Precedence order is:
   per-call override > plugin setting > env var > built-in default.

5. **Offer a preview** — offer to synthesize a short sample:
   `{"action": "preview", "text": "This is how I will sound from now on."}`
   returns `audio_base64` (mp3) plus the effective voice/model. Preview
   never persists anything.

## Other settings you can change the same way

- `model` — one of `s2.1-pro-free` (default, dev tier), `s2.1-pro`,
  `s2-pro`, `s1`.
- `latency` — `balanced` (default, ~300 ms time-to-first-audio) or `normal`.
- `chunk_length` — int 20-2000 (default 120); smaller = faster first audio.
- `voice_id: null` — unset a stored voice (falls back to env/default).

## Hard rules

- NEVER read, echo, or transmit `FISH_API_KEY` — it lives in `.env` only and
  must never appear in any output. Settings are never secrets; the key is.
- Never fabricate voice ids; only use ids from `list_voices`.
- Confirm with the user before `set_settings`.
- If `set_settings` returns an error (bad id, validation failure), report it
  verbatim and stay on the current voice.
