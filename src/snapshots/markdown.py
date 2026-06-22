"""Deterministic Markdown-to-visible-text cleaning for frozen sources."""

from __future__ import annotations

import re

_FRONTMATTER = re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|$)", re.DOTALL)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_EMPHASIS = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_HORIZONTAL_SPACE = re.compile(r"[ \t]+")


def clean_markdown(text: str) -> str:
    """Return stable visible text suitable for retrieval and verbatim evidence."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _FRONTMATTER.sub("", normalized, count=1)
    lines = normalized.splitlines()

    if lines and lines[0].strip() == "> ## Documentation Index":
        while lines and (not lines[0].strip() or lines[0].lstrip().startswith(">")):
            lines.pop(0)

    cleaned_lines: list[str] = []
    inside_fence = False
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            inside_fence = not inside_fence
            continue

        line = raw_line
        if not inside_fence:
            line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
            line = re.sub(r"^\s*>\s?", "", line)
            line = _IMAGE.sub(r"\1", line)
            line = _LINK.sub(r"\1", line)
            line = _INLINE_CODE.sub(r"\1", line)
            line = line.replace("**", "").replace("__", "")
            line = _EMPHASIS.sub(r"\1", line)
            line = _HTML_TAG.sub("", line)
            line = line.replace(r"\_", "_")
        line = _HORIZONTAL_SPACE.sub(" ", line).rstrip()
        cleaned_lines.append(line)

    compacted: list[str] = []
    previous_blank = True
    for line in cleaned_lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        compacted.append(line)
        previous_blank = is_blank
    while compacted and not compacted[-1].strip():
        compacted.pop()
    return "\n".join(compacted).strip() + "\n"
