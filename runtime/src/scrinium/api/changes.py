"""The Cambios screen (phase 7.1): what a change does to the rules, between any two refs.

`GET /changes/refs` lists what can be compared in a Git source (branches, tags, recent commits,
and the working folder as `local`); `GET /changes` compares two of them with the check engine
and the project's rules. Read only: nothing is written, nothing leaves the machine.
"""

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from scrinium.application.analysis.traces import MAX_TRACE_CHARS, triage
from scrinium.application.check.changes import ChangesResult, Side, compare, release_notes
from scrinium.application.check.git import LOCAL, GitError, Repository, git
from scrinium.application.check.history import Commit, rule_history
from scrinium.application.ingestion import sources
from scrinium.application.knowledge.anchoring import OldAnchor
from scrinium.application.knowledge.processes import processes, processes_by_rule
from scrinium.application.knowledge.rulebook import known_rules
from scrinium.application.trackers.evidence import change_tickets_of
from scrinium.application.workspaces import local_workspace
from scrinium.domain.enums import EntityType
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import KnowledgeEntity, Project, SourceConnection

# A ref as Git names it, never an option: no leading dash, no spaces or control characters.
_REF: Final = re.compile(r"^(?!-)[\w./@^~{}+-]{1,200}$")
_RECENT: Final = 30


def _source(session: Any, project_id: uuid.UUID, source_id: uuid.UUID | None) -> SourceConnection:
    try:
        project = sources.project_of(session, project_id)
    except sources.SourceNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
    query = select(SourceConnection).where(
        SourceConnection.project_id == project.id,
        SourceConnection.workspace_id == local_workspace(session).id,
        SourceConnection.provider == "local_git",
    )
    if source_id is not None:
        query = query.where(SourceConnection.id == source_id)
    found = session.scalars(query.order_by(SourceConnection.created_at)).first()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no_git_source")
    return found  # type: ignore[no-any-return]


def _repository(source: SourceConnection) -> Repository:
    root = source.configuration.get("root")
    # A folder that moved or a disk that is not mounted: say which path, not "Git failed".
    if root and not Path(root).is_dir():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"error": "source_path_missing", "path": root, "source_id": str(source.id)},
        )
    try:
        return Repository.at(Path(source.configuration["root"]))
    except (GitError, KeyError, OSError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "git_failed") from None


def _side(side: Side | None) -> dict[str, Any] | None:
    if side is None:
        return None
    return {"path": side.path, "start_line": side.start_line, "end_line": side.end_line,
            "symbol": side.symbol, "values": list(side.values)}  # fmt: skip


def describe(result: ChangesResult) -> dict[str, Any]:
    return {
        "base": result.base,
        "head": result.head,
        "changed_files": result.changed_files,
        "rules": [
            {
                "rule_id": c.rule_id,
                "title": c.title,
                "status": c.status,
                "level": c.level,
                "before": _side(c.before),
                "after": _side(c.after),
            }
            for c in result.rules
        ],
        "new_decisions": [
            {"kind": kind, **(_side(side) or {})} for kind, _path, side in result.new_decisions
        ],
        "unanchored": result.unanchored,
        "seconds": round(result.seconds, 3),
    }


class TraceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace: str = Field(min_length=1, max_length=MAX_TRACE_CHARS)


def build_changes_router(database: Database) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/projects/{project_id}/changes/refs")
    def refs(project_id: uuid.UUID, source_id: uuid.UUID | None = None) -> dict[str, Any]:
        with database.session() as session:
            source = _source(session, project_id, source_id)
            names = {
                s.id: s.display_name
                for s in session.scalars(
                    select(SourceConnection).where(
                        SourceConnection.project_id == project_id,
                        SourceConnection.provider == "local_git",
                    )
                )
            }
            session.expunge(source)
        repository = _repository(source)
        try:
            listed = git(repository.top, "for-each-ref", "--format=%(refname)",
                         "refs/heads", "refs/tags")  # fmt: skip
            log = git(repository.top, "log", f"-{_RECENT}", "--format=%H%x09%cI%x09%s")
        except GitError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "git_failed") from None
        branches = [r.removeprefix("refs/heads/") for r in listed.split() if "refs/heads/" in r]
        tags = [r.removeprefix("refs/tags/") for r in listed.split() if "refs/tags/" in r]
        commits = []
        for line in log.splitlines():
            sha, when, subject = ([*line.split("\t", 2), "", ""])[:3]
            commits.append({"sha": sha, "date": when, "subject": subject[:200]})
        return {
            "source_id": str(source.id),
            "sources": [{"id": str(i), "name": n} for i, n in names.items()],
            "branches": branches, "tags": tags, "commits": commits, "local": LOCAL,
        }  # fmt: skip

    @router.get("/api/v1/projects/{project_id}/changes")
    def changes(
        project_id: uuid.UUID,
        base: Annotated[str, Query(max_length=200)],
        head: Annotated[str, Query(max_length=200)] = LOCAL,
        source_id: uuid.UUID | None = None,
        format: Annotated[str, Query(pattern="^(json|markdown)$")] = "json",
    ) -> Any:
        if not _REF.match(base) or not (head == LOCAL or _REF.match(head)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_ref")
        with database.session() as session:
            source = _source(session, project_id, source_id)
            project = session.get_one(Project, project_id)
            rules = known_rules(session, project, source)
            # Phase 8.4: the processes each rule is a step of.
            by_rule = processes_by_rule(processes(session, project.id))
            session.expunge(source)
        repository = _repository(source)
        try:
            result = compare(repository, base, head, rules)
        except GitError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_ref") from None
        # Phase 10.5: the tickets the change names, resolved against the connected tracker.
        with database.session() as session:
            resolved = change_tickets_of(
                session, project_id, [(list(c.tickets), list(c.commits)) for c in result.rules]
            )
        if format == "markdown":
            named = [
                [(row["key"], row["ticket"]["title"] if row["ticket"] else None) for row in rows]
                for rows in resolved
            ]
            return PlainTextResponse(release_notes(result, named), media_type="text/markdown")
        described = describe(result)
        missing = 0
        for change, rule, tickets in zip(described["rules"], result.rules, resolved, strict=True):
            change["tickets"] = tickets
            change["without_ticket"] = rule.needs_ticket and not tickets
            missing += change["without_ticket"]
        described["without_ticket"] = missing
        described["branch"] = result.branch
        affected: list[str] = []
        for change in described["rules"]:
            change["processes"] = by_rule.get(change["rule_id"], [])
            if change["level"] != "moved":
                affected += [name for name in change["processes"] if name not in affected]
        return {**described, "processes_affected": affected, "source_id": str(source.id),
                "compared_at": datetime.now(UTC).isoformat()}  # fmt: skip

    @router.get("/api/v1/projects/{project_id}/rules/{entity_id}/history")
    def history(project_id: uuid.UUID, entity_id: uuid.UUID) -> dict[str, Any]:
        """Since when each decision of the rule has its values (phase 7.2), from Git alone."""
        with database.session() as session:
            source = _source(session, project_id, None)
            project = session.get_one(Project, project_id)
            rule = next(
                (r for r in known_rules(session, project, source) if r.id == str(entity_id)), None
            )
            entity = session.get(KnowledgeEntity, entity_id)
            session.expunge(source)
            if rule is None:
                if (
                    entity is None
                    or entity.project_id != project_id
                    or entity.entity_type is not EntityType.BUSINESS_RULE
                ):
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
                # The rule exists, but its code holds no decision to follow between commits
                # (or it was rejected): nothing to bisect, and that is not an error.
                return {"rule_id": str(entity_id), "title": entity.name, "anchors": [],
                        "reason": "no_decision"}  # fmt: skip
        repository = _repository(source)

        def commit(found: Commit | None) -> dict[str, str] | None:
            if found is None:
                return None
            return {"sha": found.sha, "date": found.date, "author": found.author,
                    "subject": found.subject, "path": found.path}  # fmt: skip

        anchors = []
        for number, anchor in enumerate(rule.anchors):
            old = OldAnchor(
                number, anchor.path, anchor.symbol, anchor.kind, anchor.shape, anchor.value,
                anchor.sketch, anchor.start_line,
            )  # fmt: skip
            try:
                found = rule_history(repository, old)
            except GitError:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "git_failed") from None
            anchors.append({
                "path": anchor.path, "symbol": anchor.symbol, "commits": found.commits,
                "parsed": found.parsed, "seconds": round(found.seconds, 3),
                "periods": [
                    {"values": list(p.values) if p.values is not None else None,
                     "since": commit(p.since), "until": commit(p.until)}
                    for p in found.periods
                ],
            })  # fmt: skip
        return {"rule_id": rule.id, "title": rule.title, "anchors": anchors, "reason": None}

    @router.post("/api/v1/projects/{project_id}/triage")
    def triage_trace(project_id: uuid.UUID, body: TraceBody) -> dict[str, Any]:
        """A stack trace to its functions, rules, recent changes and past fixes (phase 7.3).
        Read only; POST because a trace is long."""
        with database.session() as session:
            try:
                project = sources.project_of(session, project_id)
            except sources.SourceNotFoundError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            return triage(session, project.id, body.trace)

    return router
