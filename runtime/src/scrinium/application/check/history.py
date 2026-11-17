"""A rule's history from Git, without a model (phase 7.2, Annex B.6).

"Since when does free shipping start at 50?" The rule's decision is followed back through the
commits that touched its file (`git log --follow`), and its values are compared with the same
fingerprints as `scrinium check`. Values rarely change, so instead of parsing every commit the
boundary of each period is found by **bisection**: for a period whose values are those of the
newest commit in it, galloping back (1, 2, 4… commits) and then halving finds the oldest commit
with the same values in O(log n) parses. Then the next period starts there.

This assumes that inside a period the values do not leave and come back. If they do (a value
changed and was reverted), the bisection may join both ends; the period then reads "since"
the older of them, which is still a commit where the values were these.
"""

import time
from dataclasses import dataclass, field
from typing import Final

from scrinium.application.check.git import GitError, Repository, git
from scrinium.application.knowledge.anchoring import OldAnchor, reanchor
from scrinium.infrastructure.decisions import Decision, parse_decisions

MAX_COMMITS: Final = 2000


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str
    author: str
    subject: str
    path: str


@dataclass(frozen=True)
class Period:
    values: tuple[str, ...] | None  # None: the decision is not in the file at that commit
    since: Commit
    until: Commit | None  # the first commit with other values; None: still so


@dataclass
class History:
    periods: list[Period] = field(default_factory=list)
    commits: int = 0
    parsed: int = 0
    seconds: float = 0.0


def file_commits(repository: Repository, path: str, ref: str = "HEAD") -> list[Commit]:
    """Newest first, following renames: each commit with the file's path at that commit."""
    out = git(
        repository.top, "log", f"-{MAX_COMMITS}", "--follow", "--name-only",
        "--format=%x00%H%x09%cI%x09%an%x09%s", ref, "--", f"{repository.prefix}{path}",
    )  # fmt: skip
    commits = []
    for block in out.split("\0")[1:]:
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        sha, date, author, subject = ([*lines[0].split("\t", 3), "", "", ""])[:4]
        name = lines[1] if len(lines) > 1 else f"{repository.prefix}{path}"
        local = name[len(repository.prefix) :] if name.startswith(repository.prefix) else name
        commits.append(Commit(sha, date, author, subject[:200], local))
    return commits


def rule_history(repository: Repository, anchor: OldAnchor, ref: str = "HEAD") -> History:
    started = time.perf_counter()
    history = History()
    commits = file_commits(repository, anchor.path, ref)
    history.commits = len(commits)
    cache: dict[int, Decision | None] = {}
    current = anchor

    def at(index: int) -> Decision | None:
        """The rule's decision at commit `index`, followed from the anchor known so far."""
        if index not in cache:
            commit = commits[index]
            try:
                text = git(repository.top, "show", f"{commit.sha}:{repository.prefix}{commit.path}")
            except GitError:
                text = ""
            history.parsed += 1
            decisions = {commit.path: parse_decisions(commit.path, text)}
            moved = OldAnchor(
                current.key, commit.path, current.symbol, current.kind, current.shape,
                current.value, current.sketch, current.start_line,
            )  # fmt: skip
            match = reanchor([moved], decisions).get(current.key)
            cache[index] = match.decision if match is not None else None
        return cache[index]

    def values(index: int) -> tuple[str, ...] | None:
        found = at(index)
        return None if found is None else found.literals

    start = 0
    while start < len(commits):
        wanted = values(start)
        found = at(start)
        if found is not None:
            current = OldAnchor(
                current.key, commits[start].path, found.symbol, found.kind, found.shape,
                found.value, found.sketch, found.start_line,
            )  # fmt: skip
        # Gallop back while the values hold, then halve between the last hold and the miss.
        good, step = start, 1
        while good + step < len(commits) and values(good + step) == wanted:
            good, step = good + step, step * 2
        bad = min(good + step, len(commits))
        while bad - good > 1:
            middle = (good + bad) // 2
            if values(middle) == wanted:
                good = middle
            else:
                bad = middle
        until = commits[start - 1] if start > 0 else None
        history.periods.append(Period(wanted, commits[good], until))
        start = good + 1
    history.seconds = time.perf_counter() - started
    return history
