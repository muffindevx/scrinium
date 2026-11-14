"""Logging with redaction: logs operation metadata, never content or secrets."""

import logging
import re
import sys
from typing import Final

from scrinium.core.secret_patterns import TOKEN_PATTERNS

_REDACTED: Final = "[REDACTADO]"

# Key=value only in logs: in code files it would give too many false positives.
_KEY_VALUE_PATTERN: Final = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|authorization|cookie|session_id)"
    r"(\s*[=:]\s*)(?:bearer\s+)?[^\s&;,]+"
)


QUIET_LOGGERS: Final = ("httpx2", "httpcore2", "anthropic")


def redact(text: str) -> str:
    redacted = _KEY_VALUE_PATTERN.sub(rf"\1\2{_REDACTED}", text)
    for pattern in TOKEN_PATTERNS.values():
        redacted = pattern.sub(_REDACTED, redacted)
    return redacted


def safe_log_value(value: str, limit: int = 200) -> str:
    """Escapes line breaks and control characters so an external value cannot forge logs."""
    return value[:limit].encode("unicode_escape").decode("ascii")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def configure_logging(verbose: bool = True) -> None:
    """Without `verbose`, only warnings and errors: migrations and requests are terminal noise.

    The level also goes on the handler: `scrinium attach` lowers the root logger to DEBUG and the
    terminal must not fill up because of it.
    """
    from scrinium.core.log_hub import HideContent

    level = logging.INFO if verbose else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.addFilter(HideContent())
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # The HTTP stack of remote providers (ADR-010): at INFO it logs every request's URL, and the
    # SDK at DEBUG logs request options. Warnings and errors only, even with --verbose.
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
