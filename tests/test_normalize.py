"""Unit tests for normalize_for_speech(): the code/markdown stripping
pre-pass that turns agent reply text into something a voice assistant
should actually say. Runs with plain pytest, no network, no Hermes."""
from __future__ import annotations

import fishaudio_tts.normalize as normalize


class TestFencedCode:
    def test_fenced_code_replaced_with_spoken_marker(self):
        text = "Here you go:\n```python\nprint('hi')\n```\nEnjoy!"
        out = normalize.normalize_for_speech(text)
        assert "print" not in out
        assert "python" not in out
        assert normalize.normalize_for_speech.__doc__  # sanity
        assert "dropped the code into our chat" in out
        assert "Here you go:" in out and "Enjoy!" in out

    def test_custom_marker(self):
        out = normalize.normalize_for_speech(
            "```js\nx()\n```", code_marker="[code omitted]"
        )
        assert "[code omitted]" in out and "x()" not in out

    def test_strip_code_false_keeps_fence_contents(self):
        text = "```py\nval = 1\n```"
        out = normalize.normalize_for_speech(text, strip_code=False)
        assert "val = 1" in out


class TestInlineCode:
    def test_inline_code_backticks_flattened(self):
        out = normalize.normalize_for_speech("run `npm install` now")
        assert out == "run npm install now"

    def test_inline_code_inside_sentence(self):
        out = normalize.normalize_for_speech("The `FISH_API_KEY` var is required.")
        assert "`" not in out
        assert "FISH_API_KEY" in out


class TestMarkdown:
    def test_link_text_kept_url_dropped(self):
        out = normalize.normalize_for_speech("see [the docs](https://x.dev/a) here")
        assert out == "see the docs here"

    def test_bold_and_italic_flattened(self):
        out = normalize.normalize_for_speech("this is **bold** and *sly* text")
        assert out == "this is bold and sly text"

    def test_heading_hash_stripped(self):
        out = normalize.normalize_for_speech("## Setup\nDo it.")
        assert out == "Setup Do it."

    def test_bullet_marker_stripped(self):
        out = normalize.normalize_for_speech("- one\n- two")
        assert out == "one two"

    def test_blockquote_marker_stripped(self):
        out = normalize.normalize_for_speech("> quoted line")
        assert out == "quoted line"

    def test_whitespace_collapsed(self):
        out = normalize.normalize_for_speech("a\n\n  b\tc")
        assert out == "a b c"


class TestAbbreviations:
    def test_eg_expanded(self):
        assert normalize.normalize_for_speech("e.g. this") == "for example, this"

    def test_ie_expanded(self):
        assert normalize.normalize_for_speech("i.e. that") == "that is, that"

    def test_vs_expanded(self):
        assert normalize.normalize_for_speech("a vs. b") == "a versus b"


class TestBehavior:
    def test_empty_string_passes_through(self):
        assert normalize.normalize_for_speech("") == ""

    def test_plain_text_unchanged(self):
        text = "Just a normal sentence about the weather."
        assert normalize.normalize_for_speech(text) == text

    def test_full_reply_end_to_end(self):
        text = (
            "## Fix\nRun `make test`, e.g. from the repo root:\n"
            "```bash\nmake test\n```\nSee [docs](https://d.x) for more."
        )
        out = normalize.normalize_for_speech(text)
        assert "make test" in out
        assert "```" not in out and "https" not in out and "##" not in out
        assert "for example," in out
