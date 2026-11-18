"""Tickets as labels on rules (IMP-2): the ticket keys named by the commits that introduced or
last changed a rule's cited lines.

No connector and no network: the keys come from the local repository's history (`git blame` at
the indexed commit, then those commits' messages). A key is a label, not proof that the ticket
asked for the rule; the person reading the rule decides.
"""

import logging
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.ingestion.sources import settings_of
from scrinium.application.knowledge.entities import latest_version
from scrinium.application.knowledge.views import first_code_citation
from scrinium.domain.enums import EntityType
from scrinium.domain.ticket_keys import tickets_in
from scrinium.infrastructure.local_files.git_reader import GitError, GitRepository
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import (
    KnowledgeEntity,
    SourceConnection,
    SourceItem,
    SourceRevision,
)

logger = logging.getLogger("scrinium.tickets")

MAX_TICKETS: Final = 10
MAX_COMMITS: Final = 20
# A rule whose citation is labelled at this commit and these lines is not looked up again.
_CHECKED: Final = "tickets_checked"
# Bumped when what is stored changes: v2 adds the commits (phase 10.4), so every rule is
# looked up once more.
_CHECK_VERSION: Final = "v2"


def label_rules(database: Database, project_id: uuid.UUID) -> int:
    """Labels the project's rules with their tickets. Returns how many rules it looked up."""
    with database.session() as session:
        work = _work(session, project_id)
    looked = 0
    for root, items in work.items():
        try:
            with GitRepository(root) as repository:
                results = [
                    (entity_id, key, _lookup(repository, commit, path, start, end))
                    for entity_id, key, commit, path, start, end in items
                ]
        except GitError as error:
            logger.warning("tickets_skipped code=%s", error.code)
            continue
        with database.session() as session:
            for entity_id, key, found in results:
                entity = session.get(KnowledgeEntity, entity_id)
                if entity is None or found is None:
                    continue
                # A new dict: SQLAlchemy does not see changes made inside a JSON value.
                entity.metadata_ = {**entity.metadata_, **found, _CHECKED: key}
                looked += 1
    return looked


def _lookup(
    repository: GitRepository, commit: str, path: str, start: int, end: int
) -> dict[str, Any] | None:
    """The tickets the commits behind these lines name, which commit names each, and those
    commits (phase 10.4: a tracker may link a commit explicitly)."""
    try:
        commits = repository.line_commits(commit, path, start, end)
        messages = repository.messages(sorted(commits))
    except GitError:
        return None
    by_key: dict[str, list[str]] = {}
    for sha in sorted(messages):
        for key in tickets_in(messages[sha]):
            by_key.setdefault(key, []).append(sha)
    tickets = list(by_key)[:MAX_TICKETS]
    return {
        "tickets": tickets,
        "ticket_commits": {key: by_key[key][:MAX_COMMITS] for key in tickets},
        "commits": sorted(commits)[:MAX_COMMITS],
    }


Work = dict[Path, list[tuple[uuid.UUID, str, str, str, int, int]]]


def _work(session: Session, project_id: uuid.UUID) -> Work:
    """The rules whose citation changed since they were last labelled, by repository."""
    work: Work = defaultdict(list)
    entities = session.scalars(
        select(KnowledgeEntity).where(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
        )
    )
    for entity in entities:
        version = latest_version(session, entity.id)
        citation = None if version is None else first_code_citation(session, version.id)
        if citation is None:
            continue
        revision = session.get(SourceRevision, uuid.UUID(citation["revision_id"]))
        commit = (revision.metadata_ or {}).get("commit") if revision is not None else None
        if revision is None or not commit:
            continue
        key = (
            f"{_CHECK_VERSION}:{commit}:{citation['path']}:"
            f"{citation['start_line']}-{citation['end_line']}"
        )
        if entity.metadata_.get(_CHECKED) == key:
            continue
        connection = session.scalars(
            select(SourceConnection)
            .join(SourceItem, SourceItem.source_connection_id == SourceConnection.id)
            .where(SourceItem.id == revision.source_item_id)
        ).first()
        if connection is None:
            continue
        try:
            root = settings_of(connection).root
        except (KeyError, ValueError):
            continue
        work[Path(root)].append(
            (entity.id, key, commit, citation["path"], citation["start_line"],
             citation["end_line"])
        )  # fmt: skip
    return work
