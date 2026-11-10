"""Bug investigation: similar fix commits, the files and tests they touched, rules
with evidence in those files, and related chunks.

It finds correlations, not causes: an old commit fixing something similar does not prove
this bug comes from there. It reads local repository history without changing it and does not
use the LLM. Without a ticket connector (T-013), it does not search tickets.
"""

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select

from scrinium.application.analysis.extraction import evidence_type_for
from scrinium.application.analysis.release_comparison import LOCAL_GIT, indexed_commit
from scrinium.application.analysis.runs import finish_run
from scrinium.application.ingestion.sources import settings_of
from scrinium.application.knowledge.evidence import record_finding
from scrinium.application.retrieval import search as retrieval
from scrinium.application.retrieval.indexing import Indexer
from scrinium.core.secret_patterns import redact_tokens
from scrinium.domain.enums import AnalysisStatus, EvidenceType, FindingType, Severity
from scrinium.infrastructure.local_files.git_reader import GitError, GitRepository
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import (
    AnalysisRun,
    BusinessRule,
    EvidenceRecord,
    KnowledgeEntity,
    SourceConnection,
)

MAX_DESCRIPTION_CHARS: Final = 2000
MAX_COMMITS: Final = 500
MAX_CANDIDATES: Final = 10
MAX_TERMS: Final = 20
MAX_FRAGMENTS: Final = 5
FIX_PATTERN: Final = re.compile(
    r"\b(fix\w*|bug\w*|hotfix|corrig\w*|arregl\w*|repar\w*|regres\w*|error\w*|fallo\w*|"
    r"crash\w*|issue)\b",
    re.IGNORECASE,
)
_STOPWORDS: Final = frozenset(
    [
        "para",
        "pero",
        "como",
        "cuando",
        "donde",
        "desde",
        "hasta",
        "sobre",
        "entre",
        "porque",
        "esta",
        "este",
        "esto",
        "estos",
        "estas",
        "tiene",
        "tienen",
        "hace",
        "hacen",
        "solo",
        "cada",
        "todo",
        "todos",
        "with",
        "when",
        "from",
        "that",
        "this",
        "have",
        "does",
        "into",
        "after",
        "before",
        "error",
        "falla",
        "fallo",
    ]
)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def terms_of(description: str) -> list[str]:
    """Words that may identify the bug: identifiers and long words."""
    words = re.findall(r"[a-z_][a-z0-9_]{3,}", _normalize(description))
    seen: list[str] = []
    for word in words:
        if word not in _STOPWORDS and word not in seen:
            seen.append(word)
    return seen[:MAX_TERMS]


def _score(text: str, terms: list[str]) -> int:
    normalized = _normalize(text)
    return sum(1 for term in terms if term in normalized)


def investigate_bug(
    database: Database, indexer: Indexer, run_id: uuid.UUID, description: str
) -> None:
    terms = terms_of(description)
    limitations = [
        "Son correlaciones, no la causa demostrada del bug.",
        "Sin conector de tickets todavía: no se buscan tickets parecidos.",
        f"Solo se revisan los últimos {MAX_COMMITS} commits de cada repositorio local.",
    ]
    with database.session() as session:
        run = session.get_one(AnalysisRun, run_id)
        project_id = run.project_id
        repositories = [
            (c.id, settings_of(c).root, indexed_commit(session, c.id))
            for c in session.scalars(
                select(SourceConnection).where(
                    SourceConnection.project_id == project_id,
                    SourceConnection.provider == LOCAL_GIT,
                )
            )
        ]
        hits = retrieval.search(session, indexer, project_id, description, MAX_FRAGMENTS).hits
        fragments = [
            {"chunk_id": str(h.chunk.id), "revision_id": str(h.revision.id),
             "path": h.revision.metadata_.get("path", ""),
             "commit": h.revision.metadata_.get("commit"),
             "start_line": h.chunk.locator["start_line"], "end_line": h.chunk.locator["end_line"]}
            for h in hits[:MAX_FRAGMENTS]
        ]  # fmt: skip
    if not repositories:
        limitations.append("El proyecto no tiene repositorios Git locales.")

    candidates: list[dict[str, Any]] = []
    failed = 0
    for source_id, root, commit in repositories:
        if commit is None:
            continue
        try:
            # Git outside the transaction: reading history can take a while.
            with GitRepository(root) as repository:
                commits = repository.log(commit, MAX_COMMITS)
                scored = []
                for item in commits:
                    fix_like = bool(FIX_PATTERN.search(item.subject))
                    score = _score(item.subject, terms) * 2 + (3 if fix_like else 0)
                    if score > 0:
                        scored.append((score, fix_like, item))
                scored.sort(key=lambda entry: (-entry[0], -entry[2].committed_at))
                for score, fix_like, item in scored[: MAX_CANDIDATES * 2]:
                    files = [f.path for f in repository.changed_files(None, item.sha)]
                    tests = [p for p in files if evidence_type_for(p) is EvidenceType.TEST]
                    path_score = sum(_score(path, terms) for path in files)
                    candidates.append(
                        {"source_id": str(source_id), "commit": item.sha,
                         "committed_at": datetime.fromtimestamp(item.committed_at, UTC).isoformat(),
                         "subject": redact_tokens(item.subject)[:300], "fix_like": fix_like,
                         "score": score + path_score, "files": files[:50],
                         "tests_changed": tests}
                    )  # fmt: skip
        except GitError:
            failed += 1
            limitations.append("No se pudo leer el historial de un repositorio.")
    candidates.sort(key=lambda c: (c["score"], c["committed_at"]), reverse=True)
    candidates = candidates[:MAX_CANDIDATES]

    with database.session() as session:
        run = session.get_one(AnalysisRun, run_id)
        paths = {path for candidate in candidates for path in candidate["files"]}
        rules: dict[str, dict[str, Any]] = {}
        if paths:
            rows = session.execute(
                select(EvidenceRecord, KnowledgeEntity)
                .join(
                    BusinessRule,
                    BusinessRule.knowledge_entity_id == EvidenceRecord.target_entity_id,
                )
                .join(KnowledgeEntity, KnowledgeEntity.id == BusinessRule.knowledge_entity_id)
                .where(EvidenceRecord.project_id == project_id)
            ).all()
            for record, entity in rows:
                path = record.locator.get("path")
                if path in paths:
                    rule = rules.setdefault(
                        str(entity.id),
                        {"entity_id": str(entity.id), "name": entity.name, "paths": []},
                    )
                    if path not in rule["paths"]:
                        rule["paths"].append(path)
        findings = 0
        for candidate in candidates[:5]:
            candidate["rules"] = [
                r["entity_id"] for r in rules.values() if set(r["paths"]) & set(candidate["files"])
            ]
            if candidate["fix_like"] and not candidate["tests_changed"]:
                record_finding(
                    session, run, finding_type=FindingType.KNOWLEDGE_GAP, severity=Severity.LOW,
                    title=f"La corrección {candidate['commit'][:10]} no cambió tests",
                    description="Un commit de corrección relacionado no añadió ni modificó tests: "
                    "el fallo podría repetirse sin que nada lo detecte.",
                    location={"commit": candidate["commit"], "files": candidate["files"][:20]},
                    next_step="Comprueba si existe un test que cubra el caso del bug.",
                )  # fmt: skip
                findings += 1
        result = {
            "terms": terms,
            "candidates": candidates,
            "related_rules": list(rules.values()),
            "related_fragments": fragments,
            "findings": findings,
            "limitations": limitations,
        }
    status = AnalysisStatus.PARTIAL if failed else AnalysisStatus.COMPLETED
    finish_run(database, run_id, status, result)
