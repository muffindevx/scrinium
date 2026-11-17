"""Triage from a stack trace (phase 7.3): frames → functions → rules → recent changes → fixes.

A pasted trace from Python, JavaScript (Node or a browser) or the JVM is split into frames. Each
frame's file is found among the project's files by the longest path suffix (traces carry
absolute or packaged paths; the repository has relative ones), and its line, in the smallest
function that holds it. Then: the rules cited inside that function, the last commits that touched
the file, and earlier commits of the file whose message says they fixed something.

No model and no network: Git and the database only. It finds where to look, not the cause.
"""

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.check.git import GitError, git
from scrinium.domain.enums import ItemLifecycle
from scrinium.infrastructure.sqlite.models import (
    CodeSymbol,
    EvidenceRecord,
    KnowledgeEntity,
    SourceConnection,
    SourceItem,
)

MAX_TRACE_CHARS: Final = 20_000
MAX_FRAMES: Final = 40
MAX_COMMITS: Final = 5
_FIX_WORDS: Final = r"(fix|bug|arregl|corrig|error|hotfix|revert)"

_PYTHON: Final = re.compile(
    r'^\s*File "(?P<path>[^"]+)", line (?P<line>\d+)(?:, in (?P<fn>[\w<>.]+))?'
)
_NODE: Final = re.compile(
    r"^\s*at (?:(?:async )?(?P<fn>[\w$.<>\[\] ]+?) \()?"
    r"(?P<path>(?:file://)?[^\s()]+?):(?P<line>\d+):\d+\)?\s*$"
)
_BROWSER: Final = re.compile(r"^\s*(?P<fn>[\w$.<>]*)@(?P<path>\S+?):(?P<line>\d+):\d+\s*$")
_JVM: Final = re.compile(
    r"^\s*at (?P<cls>[\w$.]+)\.(?P<fn>[\w$<>]+)"
    r"\((?P<file>[\w$-]+\.(?:java|kt|scala|groovy)):(?P<line>\d+)\)"
)


@dataclass(frozen=True)
class Frame:
    raw: str
    path: str
    line: int
    function: str | None
    language: str


def parse_trace(text: str) -> list[Frame]:
    frames: list[Frame] = []
    for raw in text[:MAX_TRACE_CHARS].splitlines():
        if len(frames) >= MAX_FRAMES:
            break
        if match := _PYTHON.match(raw):
            frames.append(
                Frame(raw.strip(), match["path"], int(match["line"]), match["fn"], "python")
            )
        elif match := _JVM.match(raw):
            package = (
                match["cls"].rsplit(".", 1)[0].replace(".", "/") if "." in match["cls"] else ""
            )
            path = f"{package}/{match['file']}" if package else match["file"]
            frames.append(Frame(raw.strip(), path, int(match["line"]), match["fn"], "jvm"))
        elif (match := _NODE.match(raw)) or (match := _BROWSER.match(raw)):
            path = re.sub(r"^(file://|https?://[^/]+)", "", match["path"]).split("?")[0]
            if "node:internal" in path or "node_modules/" in path:
                continue  # the runtime's own frames and third-party code are not the project's
            function = (match["fn"] or "").strip() or None
            frames.append(Frame(raw.strip(), path, int(match["line"]), function, "javascript"))
    return frames


@dataclass
class Resolved:
    frame: Frame
    path: str | None = None
    source_root: str | None = None
    function: dict[str, Any] | None = None
    rules: list[dict[str, Any]] = field(default_factory=list)
    recent: list[dict[str, str]] = field(default_factory=list)
    fixes: list[dict[str, str]] = field(default_factory=list)


def _match_file(frame: Frame, files: dict[str, tuple[str, uuid.UUID, str]]) -> str | None:
    """The project file whose path is the longest suffix of the frame's (by whole segments)."""
    parts = [part for part in frame.path.replace("\\", "/").split("/") if part and part != "."]
    for start in range(len(parts)):
        candidate = "/".join(parts[start:])
        if candidate in files:
            return candidate
    # JVM frames name a package path; the repository may put it under src/main/java/…
    if frame.language == "jvm":
        tail = "/".join(parts)
        found = [path for path in files if path.endswith(f"/{tail}") or path == tail]
        return min(found, key=len) if found else None
    return None


def _log(root: str, path: str, *extra: str) -> list[dict[str, str]]:
    try:
        out = git(Path(root), "log", f"-{MAX_COMMITS}", "--format=%H%x09%cI%x09%an%x09%s",
                  *extra, "--", path)  # fmt: skip
    except GitError:
        return []
    commits = []
    for line in out.splitlines():
        sha, date, author, subject = ([*line.split("\t", 3), "", "", ""])[:4]
        commits.append({"sha": sha, "date": date, "author": author, "subject": subject[:200]})
    return commits


def triage(session: Session, project_id: uuid.UUID, text: str) -> dict[str, Any]:
    frames = parse_trace(text)
    rows = session.execute(
        select(SourceItem.provider_item_id, SourceItem.current_revision_id, SourceConnection)
        .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
        .where(
            SourceConnection.project_id == project_id,
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
            SourceItem.current_revision_id.is_not(None),
        )
    ).all()
    files: dict[str, tuple[str, uuid.UUID, str]] = {}
    for item_path, current_id, connection in rows:
        if current_id is None:
            continue
        kind = connection.provider
        git_root = str((connection.configuration or {}).get("root", ""))
        files.setdefault(item_path, (git_root if kind == "local_git" else "", current_id, kind))
    results = []
    for frame in frames:
        resolved = Resolved(frame)
        path = _match_file(frame, files)
        if path is not None:
            root, revision_id, _kind = files[path]
            resolved.path, resolved.source_root = path, root or None
            symbol = session.scalars(
                select(CodeSymbol)
                .where(
                    CodeSymbol.source_revision_id == revision_id,
                    CodeSymbol.kind != "class",
                    CodeSymbol.start_line <= frame.line,
                    CodeSymbol.end_line >= frame.line,
                )
                .order_by(CodeSymbol.end_line - CodeSymbol.start_line)
                .limit(1)
            ).first()
            if symbol is not None:
                resolved.function = {"name": symbol.qualified_name, "start_line": symbol.start_line,
                                     "end_line": symbol.end_line}  # fmt: skip
                start, end = symbol.start_line, symbol.end_line
            else:
                start = end = frame.line
            seen = set()
            for record, entity in session.execute(
                select(EvidenceRecord, KnowledgeEntity)
                .join(KnowledgeEntity, KnowledgeEntity.id == EvidenceRecord.target_entity_id)
                .where(
                    KnowledgeEntity.project_id == project_id,
                    EvidenceRecord.source_revision_id.is_not(None),
                    EvidenceRecord.locator["path"].as_string() == path,
                )
            ):
                locator = record.locator or {}
                if locator.get("path") != path or entity.id in seen:
                    continue
                cited_start = int(locator.get("start_line") or 0)
                cited_end = int(locator.get("end_line") or cited_start)
                if cited_start <= end and cited_end >= start:
                    seen.add(entity.id)
                    rule = {"id": str(entity.id), "title": entity.name}
                    resolved.rules.append(
                        {**rule, "start_line": cited_start, "end_line": cited_end}
                    )
            if root:
                resolved.recent = _log(root, path)
                resolved.fixes = _log(root, path, "-i", "-E", f"--grep={_FIX_WORDS}")
        results.append(resolved)
    resolved_count = sum(1 for r in results if r.function is not None)
    return {
        "frames": [
            {
                "raw": r.frame.raw,
                "language": r.frame.language,
                "trace_path": r.frame.path,
                "line": r.frame.line,
                "trace_function": r.frame.function,
                "path": r.path,
                "function": r.function,
                "rules": r.rules,
                "recent": r.recent,
                "fixes": r.fixes,
            }
            for r in results
        ],
        "parsed": len(frames),
        "in_project": sum(1 for r in results if r.path is not None),
        "resolved": resolved_count,
    }
