"""Coverage (phase 8.1): where the code decides, and which of those decisions have a rule.

For every active code file: its decision nodes (guards, thresholds, tables… the same as
`scrinium check`), and the rules cited in it. A decision is **covered** when a rule's latest
citation overlaps its lines; verified rules and unverified proposals are counted apart. The rest
are the «decisions without rules» layer: where a rule may be missing. How much that layer can
be trusted is measured, not assumed: on the reference sets, 121 of 124 known rules (97.6 %) sit
inside a decision node (`benchmarks/anchor_coverage.py`).

Decisions are computed once per file revision (`RevisionDecisions`): a revision never changes.
`ensure_decisions` fills what is missing, after each sync and before a coverage read.
Aggregation is by folder, one level at a time, with one root per source when there are several.
"""

import json
import posixpath
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from scrinium.domain.enums import (
    EntityLifecycle,
    EntityType,
    EvidenceType,
    ItemLifecycle,
    VerificationStatus,
)
from scrinium.domain.paths import is_test_path
from scrinium.infrastructure.decisions import DECISIONS_VERSION, parse_decisions, supports
from scrinium.infrastructure.sqlite.models import (
    EvidenceRecord,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    RevisionDecisions,
    SourceConnection,
    SourceRevision,
)

# Files parsed per call: a first visit to a huge project fills the rest in later calls.
MAX_PARSE: Final = 2000
# Recall of the «decisions without rules» layer: reference rules inside a decision node.
LAYER_RECALL: Final = {"rules": 124, "inside_a_decision": 121, "sets": 3}


@dataclass
class _Counts:
    files: int = 0
    lines: int = 0
    decisions: int = 0
    verified: int = 0
    unverified: int = 0
    uncovered: int = 0
    rules: set[str] = field(default_factory=set)

    def add(self, other: "_Counts") -> None:
        self.files += other.files
        self.lines += other.lines
        self.decisions += other.decisions
        self.verified += other.verified
        self.unverified += other.unverified
        self.uncovered += other.uncovered
        self.rules |= other.rules

    def as_dict(self) -> dict[str, Any]:
        return {"files": self.files, "lines": self.lines, "decisions": self.decisions,
                "verified": self.verified, "unverified": self.unverified,
                "uncovered": self.uncovered, "rules": len(self.rules)}  # fmt: skip


@dataclass(frozen=True)
class _File:
    source_id: str
    source_name: str
    path: str
    revision_id: str
    # None until its decisions are computed (`ensure_decisions`).
    lines: int | None
    decisions: int
    spans_json: str | None


def _code_files(session: Session, project_id: uuid.UUID) -> list[_File]:
    """The project's active code files with their stored decisions, in one query: the
    decision count comes from SQLite (`json_array_length`), and the spans stay as text until a
    file with citations needs them."""
    rows = session.execute(
        text(
            """
            SELECT c.id, c.display_name, i.provider_item_id, i.current_revision_id,
                   d.lines, json_array_length(d.spans), d.spans
            FROM source_items i
            JOIN source_connections c ON c.id = i.source_connection_id
            LEFT JOIN revision_decisions d
              ON d.source_revision_id = i.current_revision_id AND d.extractor_version = :version
            WHERE c.project_id = :project AND i.lifecycle_status = :active
              AND i.current_revision_id IS NOT NULL
            """
        ),
        {"project": project_id.hex, "active": ItemLifecycle.ACTIVE.value,
         "version": DECISIONS_VERSION},
    ).all()  # fmt: skip
    return [
        _File(source, name, path, revision, lines, count or 0, spans)
        for source, name, path, revision, lines, count, spans in rows
        if supports(path) and not is_test_path(path)
    ]


def ensure_decisions(session: Session, project_id: uuid.UUID, limit: int = MAX_PARSE) -> int:
    """Computes the decisions of current revisions that have none yet. Returns how many."""
    missing = [
        uuid.UUID(f.revision_id) for f in _code_files(session, project_id) if f.lines is None
    ][:limit]
    for revision in session.scalars(select(SourceRevision).where(SourceRevision.id.in_(missing))):
        path = str((revision.metadata_ or {}).get("path", ""))
        content = revision.text_content or ""
        decisions = parse_decisions(path, content)
        session.merge(
            RevisionDecisions(
                source_revision_id=revision.id,
                workspace_id=revision.workspace_id,
                extractor_version=DECISIONS_VERSION,
                lines=content.count("\n") + 1,
                spans=[[d.start_line, d.end_line, d.kind] for d in decisions],
            )
        )
    session.flush()
    return len(missing)


Cited = tuple[int, int, str, bool]


def _citations(
    session: Session, project_id: uuid.UUID
) -> dict[str, list[tuple[int, int, str, bool]]]:
    """Per path, the lines each live rule's latest version cites: (start, end, rule, verified)."""
    latest = (
        select(
            KnowledgeEntityVersion.knowledge_entity_id.label("entity_id"),
            func.max(KnowledgeEntityVersion.version_number).label("number"),
        )
        .where(KnowledgeEntityVersion.project_id == project_id)
        .group_by(KnowledgeEntityVersion.knowledge_entity_id)
        .subquery()
    )
    rows = session.execute(
        select(
            EvidenceRecord.locator, KnowledgeEntity.id, KnowledgeEntityVersion.verification_status
        )
        .join(KnowledgeEntityVersion, KnowledgeEntityVersion.id == EvidenceRecord.target_version_id)
        .join(
            latest,
            (latest.c.entity_id == KnowledgeEntityVersion.knowledge_entity_id)
            & (latest.c.number == KnowledgeEntityVersion.version_number),
        )
        .join(KnowledgeEntity, KnowledgeEntity.id == KnowledgeEntityVersion.knowledge_entity_id)
        .where(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
            KnowledgeEntity.lifecycle_status.not_in(
                (EntityLifecycle.DEPRECATED, EntityLifecycle.RETIRED)
            ),
            KnowledgeEntityVersion.verification_status != VerificationStatus.REJECTED,
            EvidenceRecord.evidence_type.in_((EvidenceType.CODE, EvidenceType.SCHEMA)),
        )
    ).all()
    found: dict[str, list[tuple[int, int, str, bool]]] = defaultdict(list)
    for locator, entity_id, status in rows:
        locator = locator or {}
        if "start_line" not in locator:
            continue
        start = int(locator["start_line"])
        found[str(locator.get("path", ""))].append(
            (start, int(locator.get("end_line", start)), str(entity_id),
             status is VerificationStatus.HUMAN_VERIFIED)
        )  # fmt: skip
    return found


def _file_counts(file: _File, cited: list[Cited]) -> tuple[_Counts, list[list[Any]]]:
    """The file's counts, and its spans (read only when a rule is cited in the file)."""
    counts = _Counts(files=1, lines=file.lines or 0, decisions=file.decisions)
    if not cited:
        counts.uncovered = file.decisions
        return counts, []
    spans: list[list[Any]] = json.loads(file.spans_json or "[]")
    for start, end, *_kind in spans:
        over = [c for c in cited if c[0] <= end and c[1] >= start]
        if any(verified for *_, verified in over):
            counts.verified += 1
        elif over:
            counts.unverified += 1
        else:
            counts.uncovered += 1
    counts.rules = {rule for _s, _e, rule, _v in cited}
    return counts, spans


def coverage(session: Session, project_id: uuid.UUID, path: str = "") -> dict[str, Any]:
    """The children of `path` (a source name first when there are several sources, then
    folders and files) with their counts, and the totals."""
    files = _code_files(session, project_id)
    pending = 0
    if any(f.lines is None for f in files):
        pending = ensure_decisions(session, project_id)
        files = _code_files(session, project_id)
    cited = _citations(session, project_id)
    several = len({f.source_id for f in files}) > 1
    prefix = path.strip("/")
    children: dict[str, _Counts] = defaultdict(_Counts)
    kinds: dict[str, str] = {}
    total = _Counts()
    uncovered_here: list[dict[str, Any]] = []
    for file in files:
        if file.lines is None:
            continue
        shown = f"{file.source_name}/{file.path}" if several else file.path
        counts, spans = _file_counts(file, cited.get(file.path, []))
        total.add(counts)
        if prefix and not (shown == prefix or shown.startswith(f"{prefix}/")):
            continue
        rest = shown[len(prefix) + 1 :] if prefix else shown
        if not rest:  # `path` is this file: list its decisions without a rule
            spans = spans or json.loads(file.spans_json or "[]")
            for start, end, kind in spans:
                if not any(c[0] <= end and c[1] >= start for c in cited.get(file.path, [])):
                    uncovered_here.append({"start_line": start, "end_line": end, "kind": kind})
            children[posixpath.basename(shown)].add(counts)
            kinds[posixpath.basename(shown)] = "file"
            continue
        head = rest.split("/", 1)[0]
        child = f"{prefix}/{head}" if prefix else head
        children[child].add(counts)
        kinds[child] = "folder" if "/" in rest else "file"
    return {
        "path": prefix,
        "children": sorted(
            (
                {
                    "path": name,
                    "name": posixpath.basename(name),
                    "kind": kinds[name],
                    **counts.as_dict(),
                }
                for name, counts in children.items()
            ),
            key=lambda child: (-child["decisions"], child["path"]),
        ),
        "uncovered": uncovered_here,
        "total": total.as_dict(),
        "pending": sum(1 for f in files if f.lines is None) if pending else 0,
        "layer_recall": LAYER_RECALL,
    }


def prepare_coverage(database: Any, sync_run_id: uuid.UUID) -> None:
    """After a sync: the decisions of the synced project's new revisions. Never fails the sync."""
    from scrinium.infrastructure.sqlite.models import SyncRun

    try:
        with database.session() as session:
            sync = session.get(SyncRun, sync_run_id)
            if sync is None:
                return
            connection = session.get(SourceConnection, sync.source_connection_id)
            if connection is not None and connection.project_id is not None:
                ensure_decisions(session, connection.project_id)
    except Exception:
        return
