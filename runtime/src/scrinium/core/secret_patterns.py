"""Known secret formats. Used by log redaction and by ingestion.

Only formats with a low chance of false positives: an overly broad pattern would
quarantine half the repository.
"""

import re
from typing import Final

TOKEN_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "github_pat": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "api_key_sk": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    "private_key": re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S
    ),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "stripe_live_key": re.compile(r"\b[rs]k_live_[0-9A-Za-z]{20,}\b"),
}


def redact_tokens(text: str, replacement: str = "[REDACTADO]") -> str:
    """Only known token formats: usable on free text without breaking its meaning."""
    for pattern in TOKEN_PATTERNS.values():
        text = pattern.sub(replacement, text)
    return text
