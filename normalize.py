"""Pre-pass that turns agent/LLM reply text into something a voice
assistant should actually *say* out loud.

The chat bubble still shows the full text (code included). This only
affects the audio: we drop fenced and inline code, flatten markdown,
and expand a few abbreviations so the spoken reply is natural and short.
A shorter spoken string is also a real latency win on code-heavy answers.
"""
from __future__ import annotations

import re

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_BOLD = re.compile(r"\*\*([^*]+?)\*\*")
_ITAL = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")
_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_BLOCKQUOTE = re.compile(r"^\s*>\s?", re.MULTILINE)

_ABBREV = [
    (r"\be\.g\.\s*", "for example, "),
    (r"\bi\.e\.\s*", "that is, "),
    (r"\betc\.\s*", "etcetera, "),
    (r"\bvs\.\s*", "versus "),
    (r"\bn\.b\.\s*", "note, "),
]
_ABBREV_RE = [(re.compile(p), r) for p, r in _ABBREV]


def normalize_for_speech(
    text: str,
    *,
    strip_code: bool = True,
    code_marker: str = "I've dropped the code into our chat so you can copy it.",
) -> str:
    """Return a spoken-friendly version of *text*.

    Code blocks are replaced with *code_marker*; inline code keeps its
    inner text (backticks removed). Markdown syntax is flattened and a
    handful of abbreviations are expanded.
    """
    if not text:
        return text

    if strip_code:
        text = _FENCE.sub(f" {code_marker} ", text)
        text = _INLINE.sub(lambda m: m.group(1), text)

    text = _LINK.sub(lambda m: m.group(1), text)
    text = _BOLD.sub(lambda m: m.group(1), text)
    text = _ITAL.sub(lambda m: m.group(1), text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _BLOCKQUOTE.sub("", text)

    for rx, rep in _ABBREV_RE:
        text = rx.sub(rep, text)

    text = re.sub(r"\s+", " ", text).strip()
    return text
