"""Masking personal data and long literals before a request leaves (ADR-010, optional).

Each value found becomes a stable placeholder, the same one wherever it appears in the
request (`<EMAIL_1>`), so the model can still tell that two passages mention the same address.
The map lives only in memory for that one request; the answer gets the values back before
anything is stored, so rules and answers quote the real code.

Identifiers (functions, variables, tables) are never masked: rules are about them, and
citations come from the code by line anyway.
"""

import ipaddress
import json
import re
from collections.abc import Callable
from typing import Any, Final

# Conservative on purpose: a pattern that matches half the code would make the model useless.
_EMAIL: Final = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
# A URL that carries a user and password: the whole URL goes, credentials and all.
_URL_CREDENTIALS: Final = re.compile(
    r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@'\"<>]+:[^\s/@'\"<>]+@[^\s'\"<>]+"
)
# Phone numbers need separators (`+34 600 123 456`, `(555) 123-4567`): bare digit runs are ids.
_PHONE: Final = re.compile(
    r"(?<![\w.+])(?:\+\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]\d{3,4}[\s.-]\d{3,4}(?![\w.])"
)
_IPV4: Final = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
# String literals of 24 characters or more, in single or double quotes, on one line.
_LITERAL: Final = re.compile(r"\"((?:[^\"\\\n]|\\.){24,})\"|'((?:[^'\\\n]|\\.){24,})'")
_PLACEHOLDER: Final = re.compile(r"<(?:EMAIL|URL|PHONE|IP|TEXT)_\d+>")


def _is_ip(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return True


class Masker:
    """One request's masking: `mask` each message, then `unmask` the answer."""

    def __init__(self, personal: bool, literals: bool) -> None:
        self.personal = personal
        self.literals = literals
        self._by_value: dict[tuple[str, str], str] = {}
        self._by_placeholder: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    @property
    def masked(self) -> int:
        """How many distinct values this request masked (for the local record)."""
        return len(self._by_placeholder)

    def _placeholder(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self._by_value:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            placeholder = f"<{kind}_{self._counts[kind]}>"
            self._by_value[key] = placeholder
            self._by_placeholder[placeholder] = value
        return self._by_value[key]

    def _replace(self, pattern: re.Pattern[str], kind: str, text: str,
                 keep: Callable[[str], bool] = lambda _: True) -> str:  # fmt: skip
        def swap(match: re.Match[str]) -> str:
            value = match.group(0)
            return self._placeholder(kind, value) if keep(value) else value

        return pattern.sub(swap, text)

    def mask(self, text: str) -> str:
        if self.personal:
            # URLs first: their credentials would otherwise look like an email address.
            text = self._replace(_URL_CREDENTIALS, "URL", text)
            text = self._replace(_EMAIL, "EMAIL", text)
            text = self._replace(_IPV4, "IP", text, _is_ip)
            text = self._replace(_PHONE, "PHONE", text)
        if self.literals:

            def literal(match: re.Match[str]) -> str:
                quote = match.group(0)[0]
                inner = match.group(1) if match.group(1) is not None else match.group(2)
                return f"{quote}{self._placeholder('TEXT', inner)}{quote}"

            text = _LITERAL.sub(literal, text)
        return text

    def _restore(self, text: str) -> str:
        # A literal may hold an email placeholder: repeat until nothing known is left.
        for _ in range(3):
            restored = _PLACEHOLDER.sub(
                lambda match: self._by_placeholder.get(match.group(0), match.group(0)), text
            )
            if restored == text:
                break
            text = restored
        return text

    def unmask(self, text: str) -> str:
        """The answer with the real values back. A JSON answer stays valid JSON: values are
        restored inside its strings, never spliced into its syntax."""
        if not self._by_placeholder or not _PLACEHOLDER.search(text):
            return text
        try:
            data = json.loads(text)
        except ValueError:
            return self._restore(text)
        return json.dumps(self._walk(data), ensure_ascii=False)

    def _walk(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._restore(value)
        if isinstance(value, list):
            return [self._walk(item) for item in value]
        if isinstance(value, dict):
            return {self._restore(k): self._walk(v) for k, v in value.items()}
        return value
