"""Ticket keys in commit messages and branch names (IMP-2, phase 10.5).

Pure text: no Git, no database, no network, so `scrinium check` can use it in CI.
"""

import re
from typing import Final

# Jira-style keys (PROJ-123) and issue numbers (#123). A pull request merge names its own
# number, not a ticket: "Merge pull request #12" does not count.
_KEY: Final = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-[1-9]\d*\b")
_ISSUE: Final = re.compile(r"(?<![\w&])#[1-9]\d*\b")
_MERGE_PR: Final = re.compile(r"\bpull request #\d+", re.IGNORECASE)
# Names that look like keys and are not tickets: encodings, algorithms and standards.
_NOT_TICKETS: Final = frozenset(
    {"UTF", "ISO", "SHA", "MD", "AES", "RSA", "TLS", "SSL", "HTTP", "IPV", "PEP", "RFC", "ECMA",
     "ES", "WCAG", "CP", "WINDOWS", "X", "COVID"}
)  # fmt: skip
# Words branch names use before a number that are not projects: `release-2026`, `hotfix-3`.
_BRANCH_WORDS: Final = frozenset(
    {"RELEASE", "RELEASES", "HOTFIX", "FEATURE", "FIX", "BUGFIX", "V", "VERSION", "SPRINT", "RC",
     "BUILD", "UPDATE", "CHORE", "BUMP", "PATCH", "MAJOR", "MINOR", "DEPENDABOT", "RENOVATE",
     "STAGING", "PROD", "DEV", "TEST", "WIP", "TMP", "BACKPORT", "STABLE", "BRANCH", "PR", "MR"}
)  # fmt: skip


def tickets_in(message: str) -> list[str]:
    """The ticket keys a commit message names, in order of appearance, without repeats."""
    text = _MERGE_PR.sub(" ", message)
    keys = [m.group(0) for m in _KEY.finditer(text) if m.group(0).split("-")[0] not in _NOT_TICKETS]
    found = keys + [match.group(0) for match in _ISSUE.finditer(text)]
    return list(dict.fromkeys(found))


def keys_in_branch(branch: str) -> list[str]:
    """The ticket keys a branch name carries: `feature/SHOP-221-gold-tiers` or the lower case
    `shop-221-gold-tiers` that Linear and Jira suggest."""
    found = []
    for match in _BRANCH_KEY.finditer(branch):
        key = f"{match.group(1).upper()}-{match.group(2)}"
        project = key.split("-")[0]
        if project not in _NOT_TICKETS and project not in _BRANCH_WORDS:
            found.append(key)
    return list(dict.fromkeys(found))


_BRANCH_KEY: Final = re.compile(r"(?:^|[/_.-])([A-Za-z][A-Za-z0-9]{1,9})-([1-9]\d*)(?=$|[/_.-])")
