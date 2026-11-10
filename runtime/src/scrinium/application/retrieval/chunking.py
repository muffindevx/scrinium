"""Splitting a revision into chunks that can be cited by line.

Markdown is cut by heading; the rest, into line blocks that prefer to break at a
blank line. No overlap: each line belongs to a single chunk, and the citation is exact.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Final

CHUNKER_VERSION: Final = "1"
TARGET_LINES: Final = 60
MAX_LINES: Final = 80
MAX_CHARS: Final = 6000
_MARKDOWN: Final = frozenset({".md", ".markdown", ".mdx"})
_HEADING: Final = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE: Final = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    start_line: int
    end_line: int
    heading: str | None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()

    @property
    def token_count(self) -> int:
        # Approximation: about 4 characters per token. Only for budgets, not for cutting.
        return max(1, len(self.text) // 4)

    def locator(self) -> dict[str, Any]:
        locator: dict[str, Any] = {"start_line": self.start_line, "end_line": self.end_line}
        if self.heading:
            locator["heading"] = self.heading
        return locator


def _windows(lines: list[str], first: int, last: int) -> list[tuple[int, int]]:
    """Cuts [first, last] (0-based, inclusive indexes) into blocks of up to MAX_LINES."""
    spans = []
    start = first
    while start <= last:
        end = min(start + MAX_LINES - 1, last)
        if end < last:
            # Looks for a blank line between TARGET_LINES and MAX_LINES to cut at.
            for candidate in range(end, start + TARGET_LINES - 2, -1):
                if not lines[candidate].strip():
                    end = candidate
                    break
        # Very long lines (minified, data): also cut by characters.
        size = 0
        for position in range(start, end + 1):
            size += len(lines[position]) + 1
            if size > MAX_CHARS and position > start:
                end = position - 1
                break
        spans.append((start, end))
        start = end + 1
    return spans


def _sections(lines: list[str]) -> list[tuple[int, int, str | None]]:
    """Markdown sections: each heading opens one, except inside code blocks."""
    sections: list[tuple[int, int, str | None]] = []
    start, heading, in_fence = 0, None, False
    for position, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        match = None if in_fence else _HEADING.match(line)
        if match and position > start:
            sections.append((start, position - 1, heading))
            start = position
        if match:
            heading = match.group(2)
    sections.append((start, len(lines) - 1, heading))
    return sections


def chunk_text(path: str, text: str) -> list[Chunk]:
    lines = text.splitlines()
    if not lines:
        return []
    if PurePosixPath(path).suffix.lower() in _MARKDOWN:
        sections = _sections(lines)
    else:
        sections = [(0, len(lines) - 1, None)]

    chunks: list[Chunk] = []
    for first, last, heading in sections:
        for start, end in _windows(lines, first, last):
            body = "\n".join(lines[start : end + 1])
            if not body.strip():
                continue
            chunks.append(Chunk(len(chunks), body, start + 1, end + 1, heading))
    return chunks


def embedding_input(path: str, chunk_text: str, heading: str | None) -> str:
    """Text the model receives: the path and the heading give the chunk context."""
    header = f"{path}\n# {heading}\n" if heading else f"{path}\n"
    return header + chunk_text
