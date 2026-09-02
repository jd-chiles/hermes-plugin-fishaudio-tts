# fishaudio-tts — Fish Audio voice backend for Hermes Agent

[![CI](https://github.com/jd-chiles/hermes-plugin-fishaudio-tts/actions/workflows/ci.yml/badge.svg)](https://github.com/jd-chiles/hermes-plugin-fishaudio-tts/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)

A [Hermes Agent](https://hermes-agent.nousresearch.com/docs) plugin that makes your agent *speak* through [Fish Audio](https://fish.audio) — with one twist that matters in practice: it **refuses to read code out loud**.

## The problem

Ask a coding assistant a question over voice and you get a minute of a robot reciting `triple backtick python print open parenthesis ...`. The chat bubble is fine — it's the *audio* that's broken. Meanwhile, most TTS integrations synthesize the entire reply as one request, so on long technical answers you wait for the whole thing before hearing a word.

## The solution

This plugin registers a `fishaudio` TTS provider that:

1. **Cleans the text before synthesis, not the chat.** Fenced ```code``` blocks are replaced with a spoken marker (*"I've dropped the code into our chat so you can copy it."*), inline `` `code` `` keeps its contents minus the backticks, and markdown (links, bold, italics, headings, bullets, blockquotes) is flattened. A few abbreviations (`e.g.`, `i.e.`, `vs.`) are expanded so they sound natural. The chat still shows the full raw text — only the audio is cleaned. Shorter spoken text is also a real latency win on code-heavy answers.
2. **Synthesizes sentence-by-sentence.** Each sentence becomes its own small request to `POST https://api.fish.audio/v1/tts` with Fish's `latency: balanced` mode (~300 ms to first audio) and `chunk_length: 120`. Long replies never block on one giant request.
3. **Plays nice with voice bubbles.** The provider opts into `voice_compatible`, so the Hermes gateway converts the output to Opus and it lands in Telegram as a proper voice message.

## Architecture

```
reply text
   │
   ▼
register(ctx) ──► ctx.register_tts_provider(FishAudioTTSProvider())
   │                       │
   │                       ▼
   │            Hermes TTS registry  ("tts.provider: fishaudio")
   │                       │
   │                       ▼
   │            tools/tts_tool dispatch
   │                       │
   ▼                       ▼
normalize_for_speech()   per-sentence sync synthesis ──► POST /v1/tts
(code + markdown strip)  (NOT the chunked PCM streamer)    raw audio bytes
```

Plugin providers in Hermes take the **per-sentence synchronous path**: each sentence is a complete request/response round-trip whose bytes are concatenated into the output file. The gateway's chunked PCM streamer is a separate, built-in path this plugin deliberately does not use — keeping the provider a plain `requests` call that is trivial to test and reason about.

## Install

### Option A — the standard way (recommended)

```bash
# 1. Install via the Hermes plugin manager
hermes plugins install jd-chiles/hermes-plugin-fishaudio-tts

# 2. Configure it as your voice provider
#    Run `hermes` and open Settings → Voice → Text-to-Speech provider → Fish Audio.
#    The provider's setup schema prompts you for:
#      - FISH_API_KEY    (get one at https://fish.audio/app/api-keys/)
#      - FISH_VOICE_ID   (optional voice model id)
#      - FISH_TTS_MODEL  (optional; default s2.1-pro-free)
#    Secrets go into ~/.hermes/.env, never config.yaml.

# 3. Make it the default TTS provider
hermes config set tts.provider fishaudio
```

### Option B — manual git clone

```bash
git clone https://github.com/jd-chiles/hermes-plugin-fishaudio-tts.git \
    ~/.hermes/plugins/tts/fishaudio

hermes plugins enable tts/fishaudio
hermes config set tts.provider fishaudio
echo 'FISH_API_KEY=your-key-here' >> ~/.hermes/.env
```

The default model `s2.1-pro-free` is Fish Audio's free developer tier.

## Configuration

| Env var | Required | Default | Purpose |
|---|---|---|---|
| `FISH_API_KEY` | yes | — | Fish Audio API key (bearer auth) |
| `FISH_VOICE_ID` | no | *(Fish default voice)* | Voice model id, sent as `reference_id` |
| `FISH_TTS_MODEL` | no | `s2.1-pro-free` | Fish Audio TTS model (see [models overview](https://docs.fish.audio/developer-guide/models-pricing/models-overview)) |

Per-call overrides (`voice`, `model`, `speed`) passed by the gateway take precedence over the env vars. `speed` maps to Fish's `prosody.speed`.

## Usage

**Desktop / CLI** — with `tts.provider: fishaudio` set, every `text_to_speech` call speaks through Fish. Turn on spoken replies in a session with:

```
/voice tts      # agent replies out loud; you still type
/voice on       # full voice loop: you speak -> transcription -> spoken reply
/voice status   # show voice mode state
/voice off
```

**Telegram** — the same modes apply per-chat (`voice.auto_tts` in config for auto-speak). Because the provider is `voice_compatible`, spoken replies arrive as native Telegram voice bubbles (Opus), not file attachments. Transcription on the inbound side is configured separately via `stt.provider`.

## Files

| File | Role |
|---|---|
| `plugin.yaml` | Manifest (`kind: standalone`, author, description) |
| `__init__.py` | `register(ctx)` → `ctx.register_tts_provider(...)` |
| `provider.py` | `FishAudioTTSProvider` — synthesis, config resolution, latency tuning |
| `normalize.py` | `normalize_for_speech()` — code/markdown stripping pre-pass |
| `tests/` | Pure pytest suite — no Hermes install, no network (all HTTP mocked) |

## Tests

```bash
python3 -m pytest tests/ -v
```

The suite covers normalization behavior (fenced/inline code, markdown, abbreviations), config resolution (env precedence, defaults), request payload shape (auth header, model, latency, chunk_length, format, reference_id, prosody), sentence chunking, error handling, and `is_available()` when the key is unset. No `FISH_API_KEY` is needed to run it.

## Notes

- This is a **user plugin** (`~/.hermes/plugins/tts/fishaudio/`), not a patch to Hermes core — a Hermes upgrade won't clobber it, and it won't shadow built-in providers.
- The plugin is stateless; one HTTP call per sentence with a 60 s timeout, errors surfaced as `RuntimeError` with the Fish API status code.
- For transcription (speech → text), see the companion plugin [hermes-plugin-telegram-voice-reply](https://github.com/jd-chiles/hermes-plugin-telegram-voice-reply).

## License

[MIT](LICENSE) © 2026 Jon David Cho Chiles
