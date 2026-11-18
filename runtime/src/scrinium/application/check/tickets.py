"""The tickets behind a change (phase 10.5, ADR-014): for `scrinium check` and the Changes screen.

A rule that changes should say why. Here "why" is read from Git alone, the "mentioned" level of
annex C.4:

- the messages of the commits in the range that touched the rule's file;
- the branch name, which in CI is often the only place a ticket appears
  (`feature/SHOP-221-gold-tiers`).

No database, no network: it runs in CI and in the pre-commit hook. The Changes screen then
resolves the keys against the connected tracker to show titles and statuses.
"""

import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Final

from scrinium.application.check.git import COMMITTED, LOCAL, STAGED, GitError, Repository, git
from scrinium.domain.ticket_keys import keys_in_branch, tickets_in

# Per file: the newest commits in the range are enough to name the ticket.
MAX_COMMITS_PER_PATH: Final = 50
MAX_TICKETS: Final = 10
# Where CI services put the branch of a pull request when the clone is a detached HEAD.
_BRANCH_VARIABLES: Final = (
    "GITHUB_HEAD_REF",
    "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME",
    "BITBUCKET_BRANCH",
    "BUILDKITE_BRANCH",
    "CI_COMMIT_REF_NAME",
    "BRANCH_NAME",
)


@dataclass(frozen=True)
class TicketRef:
    key: str
    # "commit" or "branch"
    origin: str
    # The commit's SHA, or the branch name.
    where: str


@dataclass
class ChangeTickets:
    branch: str | None = None
    by_path: dict[str, list[TicketRef]] = field(default_factory=dict)
    # Every commit in the range that touched each file, named or not: a tracker may link it.
    commits: dict[str, list[str]] = field(default_factory=dict)

    def commits_for(self, *paths: str) -> list[str]:
        return list(dict.fromkeys(sha for path in paths for sha in self.commits.get(path, [])))

    def for_paths(self, *paths: str) -> list[TicketRef]:
        """The tickets of these files' commits, then the branch's; each key once."""
        found: dict[str, TicketRef] = {}
        for path in paths:
            for ref in self.by_path.get(path, []):
                found.setdefault(ref.key, ref)
        for key in keys_in_branch(self.branch or ""):
            found.setdefault(key, TicketRef(key, "branch", self.branch or ""))
        return list(found.values())[:MAX_TICKETS]


def current_branch(repository: Repository) -> str | None:
    """The branch being checked: CI's variable when the clone is detached, else Git's."""
    for name in _BRANCH_VARIABLES:
        value = os.environ.get(name, "").strip()
        if value:
            return value[:250]
    try:
        branch = git(repository.top, "symbolic-ref", "--short", "-q", "HEAD").strip()
    except GitError:
        return None
    return branch or None


def change_tickets(
    repository: Repository, since: str, head: str, paths: set[str], branch: str | None
) -> ChangeTickets:
    """`paths` are relative to the repository's top level. `head` as `Repository.changes` takes
    it: uncommitted work (local or staged) has no message, so only its branch speaks for it."""
    result = ChangeTickets(branch=branch)
    if not paths:
        return result
    end = "HEAD" if head in (COMMITTED, LOCAL, STAGED) else repository.resolve(head)
    commits_of: dict[str, list[str]] = defaultdict(list)
    for path in sorted(paths):
        try:
            listed = git(
                repository.top, "log", f"--max-count={MAX_COMMITS_PER_PATH}", "--format=%H",
                f"{since}..{end}", "--", path,
            )  # fmt: skip
        except GitError:
            continue
        commits_of[path] = [line for line in listed.split() if len(line) == 40]
    result.commits = dict(commits_of)
    wanted = sorted({sha for shas in commits_of.values() for sha in shas})
    messages = _messages(repository, wanted)
    for path, shas in commits_of.items():
        refs: list[TicketRef] = []
        for sha in shas:
            refs += [TicketRef(key, "commit", sha) for key in tickets_in(messages.get(sha, ""))]
        result.by_path[path] = refs
    return result


def _messages(repository: Repository, commits: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for start in range(0, len(commits), 200):
        chunk = commits[start : start + 200]
        try:
            output = git(
                repository.top, "log", "--no-walk=unsorted", "--no-color",
                "--format=%H%x1f%B%x1e", "--end-of-options", *chunk,
            )  # fmt: skip
        except GitError:
            continue
        for record in output.split("\x1e"):
            sha, _, message = record.strip("\n").partition("\x1f")
            if len(sha) == 40:
                found[sha] = message
    return found
