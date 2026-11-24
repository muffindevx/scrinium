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


# Words a Spanish or English sentence cannot end on: a title that does was cut (B1).
_DANGLING: Final = frozenset(
    {
        "a", "al", "como", "con", "cuando", "de", "del", "el", "en", "entre", "la", "las",
        "lo", "los", "o", "para", "por", "que", "se", "si", "sin", "su", "sus", "un", "una",
        "unos", "unas", "y",
        "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "or", "than",
        "that", "the", "to", "when", "with",
    }
)  # fmt: skip


def is_truncated(title: str, limit: int) -> bool:
    """The title was cut: it hit the schema cap, carries phase 1's mark, or ends on a word
    no sentence ends on ("...debe ser uno de los")."""
    words = title.rstrip(" .").lower().split()
    return len(title) >= limit or title.endswith("…") or (len(words) > 2 and words[-1] in _DANGLING)


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


# --- F2: a statement's numbers and names are in its code (phase 4.3) --------------------------

_STATEMENT_NUMBER: Final = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)(\s?%)?(?![\w])")
_STATEMENT_NAME: Final = re.compile(r"`([A-Za-z_][\w.]*)`")
_CODE_NUMBER: Final = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
# B3: a literal in quotes ('agent.finished', "stale") and a dotted identifier (agent.started):
# the code must write them. One word with no spaces, so a quoted phrase is not checked.
_STATEMENT_QUOTED: Final = re.compile(
    r"(?<!\w)['\"\u2018\u201c]([^\s'\"\u2018\u2019\u201c\u201d`]{2,80})['\"\u2019\u201d](?!\w)"
)
_STATEMENT_DOTTED: Final = re.compile(r"(?<![\w.`])([A-Za-z_]\w+(?:\.[A-Za-z_]\w+)+)(?![\w`])")


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def unanchored_terms(statement: str, literals: tuple[str, ...], code: str) -> list[str]:
    """The numbers and `names` of a statement that its code does not hold: [] when anchored.

    A number counts if the decision decides with it (its literals, constants resolved) or the
    cited code writes it; a percentage also as a fraction (`2 %` is `0.02`) or as the factor
    that adds or takes it (`10 %` more is `* 1.1`, 25 % off is `* 0.75`). A name in
    backquotes must be written in the cited code. Words are not checked: they are the
    statement's own language.
    """
    known: set[float] = set()
    for value in (*literals, *_CODE_NUMBER.findall(code)):
        number = _number(value)
        if number is not None:
            known.add(round(number, 9))
    missing = []
    for match in _STATEMENT_NUMBER.finditer(statement):
        number = _number(match[1])
        if number is None:
            continue
        options = {round(number, 9)}
        if match[2]:
            fraction = number / 100
            options |= {round(fraction, 9), round(1 + fraction, 9), round(1 - fraction, 9)}
        if not options & known:
            missing.append(match[0].strip())
    missing += [name for name in _STATEMENT_NAME.findall(statement) if name not in code]
    return missing


# B3: how far a decision's own code reaches. Above, the condition or `case` that governs it;
# below, its branch, until a sibling branch starts. A statement that takes a literal from the
# next `case` ("pasa a running con 'agent.finished'" on the line of agent.started) is caught.
_ABOVE_LINES: Final = 6
_BELOW_LINES: Final = 4
_SIBLING: Final = re.compile(r"^\s*(?:\}\s*)?(?:case\b|default\s*:|else\b|elif\b|except\b|catch\b)")
_BRANCH_END: Final = re.compile(r"^\s*(?:break|continue)\b")


def decision_reach(lines: list[str], start: int, end: int) -> str:
    """Lines `start`-`end` (1-based) with what governs them above and their branch below."""
    below = []
    for line in lines[end : end + _BELOW_LINES]:
        if _SIBLING.match(line):
            break
        below.append(line)
        if _BRANCH_END.match(line):
            break
    return "\n".join([*lines[max(0, start - 1 - _ABOVE_LINES) : end], *below])


def unanchored_literals(statement: str, reach: str) -> list[str]:
    """The literals in quotes ('stale') and dotted identifiers (agent.finished) of a statement
    that the decision's reach does not write: [] when anchored. A quoted phrase with spaces
    is words, not a literal, and is not checked."""
    bare = _STATEMENT_NAME.sub(" ", statement)
    lowered = reach.lower()
    missing: list[str] = []
    for literal in (*_STATEMENT_QUOTED.findall(bare), *_STATEMENT_DOTTED.findall(bare)):
        if literal.lower() not in lowered and literal not in missing:
            missing.append(literal)
    return missing
