"""Deterministic checks of an extracted rule.

A 4B model does not always obey the prompt: these checks look at what it returned without
calling it again. Today the benchmark uses them to measure; F2 will use them to discard.
"""

import re
from typing import Final, Literal

# UI, drawing and sound words in Spanish and English (the model writes in both).
_VISUAL: Final = re.compile(
    r"\b(?:dibuj\w*|draw\w*|render\w*|hud|layout|disposici[oó]n|pantalla\w*|screen\w*|"
    r"animaci[oó]n\w*|anima\w*|sprite\w*|textur\w*|fuentes? de texto|fuentes? tipogr\w*|"
    r"font\w*|c[aá]mara|camera|sonido\w*|sound\w*|audio|m[uú]sica|music|overlay|"
    r"interfaz|\bui\b|bot[oó]n\w*|button\w*|color\w*|colour\w*|brillo|glow|parpade\w*)\b",
    re.IGNORECASE,
)
# The minimum for some lines to back a rule: a condition, a comparison, an error
# or a named or numeric constant.
_EVIDENCE: Final = re.compile(
    r"(?i:\b(?:if|else|elif|switch|case|when|while|unless|throw|raise|assert|check)\b)"
    # A permission ('users:manage') or a status or role ('ADMIN') is evidence too (Q1).
    r"""|['"][a-z][\w-]*:[a-z][\w-]*['"]|['"][A-Z][A-Z0-9_]+['"]|<>"""
    r"|[<>]=?|===?|!==?|\?\?|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\b\d+(?:\.\d+)?\b"
)
_SPANISH: Final = frozenset({
    "el", "la", "los", "las", "de", "del", "que", "en", "un", "una", "por", "para", "con", "sin",
    "se", "si", "no", "es", "son", "al", "lo", "su", "sus", "mas", "cuando", "debe", "solo",
    "como", "entre", "tras", "hasta", "desde", "cada",
})  # fmt: skip
_ENGLISH: Final = frozenset({
    "the", "a", "an", "of", "to", "in", "on", "for", "with", "without", "is", "are", "be", "if",
    "when", "must", "only", "each", "from", "by", "and", "or", "not", "it", "its", "at", "as",
    "than",
})  # fmt: skip

Language = Literal["es", "en", "?"]


def is_truncated(title: str, limit: int) -> bool:
    """The title hit the schema cap: the grammar cut it, almost certainly mid-sentence."""
    return len(title) >= limit


def language(text: str) -> Language:
    """Language by frequent words. "?" if there are not enough to decide."""
    words = re.findall(r"[a-záéíóúñü]+", text.lower())
    spanish = sum(word in _SPANISH for word in words)
    english = sum(word in _ENGLISH for word in words)
    if spanish + english < 2:
        return "?"
    return "es" if spanish >= english else "en"


def mentions_visual(text: str) -> bool:
    return _VISUAL.search(text) is not None


def has_evidence(cited: str) -> bool:
    """The cited lines hold something that may be a rule, not just calls or assignments."""
    return _EVIDENCE.search(cited) is not None


def cited_text(text: str, first_line: int, start: int, end: int) -> str:
    """Lines [start, end] of a chunk that starts at line `first_line` of the file."""
    lines = text.split("\n")
    return "\n".join(lines[max(0, start - first_line) : max(0, end - first_line + 1)])
