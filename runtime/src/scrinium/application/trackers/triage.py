"""Triage from a bug ticket (phase 10.5, annex C.5 item 3).

A ticket → the commits that worked on it → the files they touched → the rules cited in those
files, and earlier bugs with a similar title that were resolved.

- **Commits:** those the tracker links to the ticket (explicit), and those whose message names
  its key or one of its previous keys (mentioned), in each Git source of the project.
- **Rules:** the live rules cited in the files those commits touched; the ones whose cited lines
  a commit changed come first.
- **Similar resolved bugs:** a local comparison of title words. Descriptions are not stored by
  default (ADR-014, decision 6), so the title is what there is.

No model and no network: the database and local Git. It shows where to look, not the cause.
"""

import re
import uuid
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.check.git import GitError, git
from scrinium.application.trackers.evidence import COMMIT, describe_ticket
from scrinium.domain.enums import EntityLifecycle, EntityType
from scrinium.infrastructure.sqlite.models import (
    EvidenceRecord,
    KnowledgeEntity,
    SourceConnection,
    Ticket,
    TicketLink,
)

MAX_COMMITS: Final = 20
MAX_FILES: Final = 60
MAX_SIMILAR: Final = 5
MIN_SIMILARITY: Final = 0.2
_WORD: Final = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
# Words every bug title has: they say nothing about which bug it is.
_COMMON: Final = frozenset(
    {"bug", "error", "fallo", "falla", "the", "and", "for", "with", "when", "not", "does",
     "que", "con", "para", "por", "los", "las", "del", "una", "uno", "cuando", "sin", "se"}
)  # fmt: skip
_HUNK: Final = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def triage_ticket(session: Session, project_id: uuid.UUID, ticket_id: uuid.UUID) -> dict[str, Any]:
    ticket = session.get(Ticket, ticket_id)
    if ticket is None or ticket.project_id != project_id:
        raise LookupError
    keys = [ticket.key, *(ticket.previous_keys or [])]
    linked = set(
        session.scalars(
            select(TicketLink.from_ref).where(
                TicketLink.ticket_id == ticket.id, TicketLink.from_kind == COMMIT
            )
        )
    )
    roots = [
        str((connection.configuration or {}).get("root", ""))
        for connection in session.scalars(
            select(SourceConnection).where(
                SourceConnection.project_id == project_id,
                SourceConnection.provider == "local_git",
            )
        )
    ]
    commits: list[dict[str, Any]] = []
    for root in filter(None, roots):
        commits += _commits(Path(root), keys, linked)
    commits = commits[:MAX_COMMITS]
    rules = _rules(session, project_id, commits)
    return {
        "ticket": describe_ticket(ticket),
        "commits": [
            {key: value for key, value in commit.items() if key != "changed"} for commit in commits
        ],
        "rules": rules,
        "similar": _similar(session, project_id, ticket),
    }


def _commits(root: Path, keys: list[str], linked: set[str]) -> list[dict[str, Any]]:
    """The commits of this repository that name a key, and those the tracker links."""
    found: dict[str, str] = {}
    greps = [f"--grep={key}" for key in keys]
    try:
        listed = git(root, "log", f"--max-count={MAX_COMMITS * 3}", "--format=%H%x1f%B%x1e",
                     "--fixed-strings", *greps)  # fmt: skip
    except GitError:
        listed = ""
    pattern = re.compile(r"(?<![\w-])(" + "|".join(map(re.escape, keys)) + r")(?![\w])")
    for record in listed.split("\x1e"):
        sha, _, message = record.strip("\n").partition("\x1f")
        # --grep matches substrings: SHOP-22 would also find SHOP-221. Whole keys only.
        if len(sha) == 40 and pattern.search(message):
            found[sha] = "mentioned"
    for sha in linked:
        if re.fullmatch(r"[0-9a-f]{40}", sha) and sha not in found:
            try:
                git(root, "cat-file", "-e", f"{sha}^{{commit}}")
            except GitError:
                continue  # not in this repository
            found[sha] = "explicit"
    commits = []
    for sha, level in found.items():
        try:
            head = git(root, "show", "-s", "--format=%cI%x09%an%x09%s", sha).strip()
            # --relative: paths as the source sees them when it is a subfolder of the repository.
            diff = git(root, "show", "--relative", "--format=", "--unified=0", "--no-color",
                       "--no-ext-diff", sha)  # fmt: skip
        except GitError:
            continue
        date, author, subject = ([*head.split("\t", 2), "", "", ""])[:3]
        changed = _changed_lines(diff)
        commits.append({
            "sha": sha, "date": date, "author": author, "subject": subject[:200],
            "level": level, "files": sorted(changed)[:MAX_FILES], "changed": changed,
        })  # fmt: skip
    commits.sort(key=lambda commit: str(commit["date"]), reverse=True)
    return commits


def _changed_lines(diff: str) -> dict[str, list[tuple[int, int]]]:
    """Per file, the line ranges a commit wrote, in its own numbering (the new side): later code
    keeps it closer than the parent's, and a file the commit added has no old side. A hunk
    that only deletes marks the line where the deletion was."""
    changed: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            current = target[2:] if target.startswith("b/") else None
            if current is not None:
                changed.setdefault(current, [])
        elif current is not None and (match := _HUNK.match(line)):
            start, count = int(match.group(1)), int(match.group(2) or 1)
            changed[current].append((start, start + max(count, 1) - 1))
    return changed


def _rules(
    session: Session, project_id: uuid.UUID, commits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    touched: dict[str, list[tuple[int, int, str]]] = {}
    for commit in commits:
        for path, ranges in commit["changed"].items():
            touched.setdefault(path, [])
            touched[path] += [(start, end, commit["sha"]) for start, end in ranges]
    if not touched:
        return []
    found: dict[uuid.UUID, dict[str, Any]] = {}
    paths = list(touched)[:MAX_FILES]
    for record, entity in session.execute(
        select(EvidenceRecord, KnowledgeEntity)
        .join(KnowledgeEntity, KnowledgeEntity.id == EvidenceRecord.target_entity_id)
        .where(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
            KnowledgeEntity.lifecycle_status.not_in(
                (EntityLifecycle.DEPRECATED, EntityLifecycle.RETIRED)
            ),
            EvidenceRecord.locator["path"].as_string().in_(paths),
        )
    ):
        locator = record.locator or {}
        path = str(locator.get("path", ""))
        start = int(locator.get("start_line") or 0)
        end = int(locator.get("end_line") or start)
        hits = sorted({sha for a, b, sha in touched.get(path, []) if a <= end and b >= start})
        current = found.get(entity.id)
        if current is None or (hits and not current["commits"]):
            found[entity.id] = {
                "id": str(entity.id), "title": entity.name, "path": path,
                "start_line": start, "end_line": end, "commits": hits,
            }  # fmt: skip
    return sorted(found.values(), key=lambda rule: (not rule["commits"], rule["path"],
                                                     rule["start_line"]))  # fmt: skip


def _words(title: str) -> set[str]:
    return {word for word in _WORD.findall(title.casefold()) if word not in _COMMON}


def _similar(session: Session, project_id: uuid.UUID, ticket: Ticket) -> list[dict[str, Any]]:
    """Resolved bugs whose titles share words with this one (Jaccard), best first."""
    mine = _words(ticket.title)
    if not mine:
        return []
    scored = []
    for other in session.scalars(
        select(Ticket).where(
            Ticket.project_id == project_id,
            Ticket.id != ticket.id,
            Ticket.ticket_type == "bug",
            Ticket.status_category == "done",
        )
    ):
        theirs = _words(other.title)
        if not theirs:
            continue
        score = len(mine & theirs) / len(mine | theirs)
        if score >= MIN_SIMILARITY:
            scored.append((score, other))
    scored.sort(key=lambda pair: (-pair[0], pair[1].key))
    return [
        {**describe_ticket(other), "similarity": round(score, 2)}
        for score, other in scored[:MAX_SIMILAR]
    ]
