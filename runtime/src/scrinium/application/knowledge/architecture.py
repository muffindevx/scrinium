"""Architecture view (stage 3, phase D): the code as modules with dependencies, one level at a
time, from the project down to a file's functions and their rules. No model.

A module is a folder or a file at the level being looked at; inside a file, its top-level
functions. Arrows are the resolved calls (`code_edges`) between modules at that level, with
their count. Tests and tooling folders are left out, as everywhere.
"""

import posixpath
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.analysis.candidates import is_rule_source
from scrinium.application.knowledge.entities import latest_version
from scrinium.application.knowledge.files import shown_path, source_names, source_of
from scrinium.application.knowledge.views import first_code_citation
from scrinium.domain.enums import EntityType, ItemLifecycle
from scrinium.domain.paths import is_test_path
from scrinium.infrastructure.code_structure import NESTED
from scrinium.infrastructure.sqlite.models import (
    CodeEdge,
    CodeSymbol,
    KnowledgeEntity,
    SourceConnection,
    SourceItem,
    SourceRevision,
)

MAX_RULES_LISTED: Final = 50


@dataclass
class _Function:
    id: uuid.UUID
    name: str
    path: str
    start: int
    end: int
    revision_id: uuid.UUID


@dataclass
class _Module:
    key: str
    name: str
    kind: str  # "folder", "file" or "function"
    files: set[str] = field(default_factory=set)
    functions: int = 0
    rules: list[dict[str, Any]] = field(default_factory=list)
    calls_in: int = 0
    calls_out: int = 0
    start_line: int | None = None
    end_line: int | None = None
    revision_id: uuid.UUID | None = None


def _code(
    session: Session, project_id: uuid.UUID
) -> tuple[list[_Function], dict[uuid.UUID, _Function]]:
    """The project's top-level functions in current revisions, and every symbol (nested too)
    mapped to its top-level function, so a nested call counts for the function that holds it."""
    names = source_names(session, project_id)
    rows = session.execute(
        select(CodeSymbol, SourceRevision, SourceItem.source_connection_id)
        .join(SourceRevision, SourceRevision.id == CodeSymbol.source_revision_id)
        .join(SourceItem, SourceItem.current_revision_id == SourceRevision.id)
        .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
        .where(
            SourceConnection.project_id == project_id,
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
            CodeSymbol.kind != "class",
        )
    ).all()
    tops: list[_Function] = []
    nested: list[tuple[CodeSymbol, str]] = []
    for symbol, revision, source_id in rows:
        path = revision.metadata_.get("path", "")
        if not path or is_test_path(path) or not is_rule_source(path):
            continue
        # With several sources, each is a root of its own: `src/utils.ts` of two never mix.
        path = shown_path(names, source_id, path)
        if symbol.kind == NESTED:
            nested.append((symbol, path))
        else:
            tops.append(
                _Function(symbol.id, symbol.qualified_name, path, symbol.start_line,
                          symbol.end_line, revision.id)
            )  # fmt: skip
    owner = {function.id: function for function in tops}
    by_path: dict[str, list[_Function]] = defaultdict(list)
    for function in tops:
        by_path[function.path].append(function)
    for symbol, path in nested:
        holder = next(
            (f for f in by_path[path] if f.start <= symbol.start_line and f.end >= symbol.end_line),
            None,
        )
        if holder is not None:
            owner[symbol.id] = holder
    return tops, owner


def _rule_places(session: Session, project_id: uuid.UUID) -> list[tuple[dict[str, Any], str, int]]:
    """Each rule with the path and first line of its current citation."""
    names = source_names(session, project_id)
    places = []
    for entity in session.scalars(
        select(KnowledgeEntity).where(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
        )
    ):
        version = latest_version(session, entity.id)
        citation = None if version is None else first_code_citation(session, version.id)
        if citation is None or not citation.get("path"):
            continue
        rule = {
            "entity_id": str(entity.id), "name": entity.name,
            "verification_status": version.verification_status.value if version else None,
        }  # fmt: skip
        source_id = source_of(session, uuid.UUID(citation["revision_id"]))
        path = shown_path(names, source_id, str(citation["path"]))
        places.append((rule, path, int(citation["start_line"])))
    return places


def _descend(prefix: str, files: set[str]) -> str:
    """Skips folders with a single subfolder and no files (`src/` -> `src/game/`)."""
    while True:
        children = {f[len(prefix) :].split("/", 1)[0] for f in files if f.startswith(prefix)}
        loose = any("/" not in f[len(prefix) :] for f in files if f.startswith(prefix))
        if len(children) != 1 or loose:
            return prefix
        only = next(iter(children))
        prefix = f"{prefix}{only}/"


def architecture(session: Session, project_id: uuid.UUID, path: str = "") -> dict[str, Any]:
    """One level of the code: modules, their dependencies and their rules. `path` is "" (the
    project), a folder ending in "/" or a file. Raises LookupError if nothing is there."""
    tops, owner = _code(session, project_id)
    places = _rule_places(session, project_id)
    files = {f.path for f in tops} | {p for _, p, _ in places}
    is_file = bool(path) and path in files
    if not is_file:
        prefix = path if not path or path.endswith("/") else f"{path}/"
        if prefix and not any(f.startswith(prefix) for f in files):
            raise LookupError(path)
        prefix = _descend(prefix, files)

    def module_of(file_path: str, line: int | None = None) -> str | None:
        if is_file:
            if file_path != path or line is None:
                return None
            holder = next((f for f in tops if f.path == path and f.start <= line <= f.end), None)
            return holder.name if holder else None
        if not file_path.startswith(prefix):
            return None
        rest = file_path[len(prefix) :]
        head = rest.split("/", 1)[0]
        return f"{prefix}{head}/" if "/" in rest else f"{prefix}{head}"

    modules: dict[str, _Module] = {}

    def module(key: str) -> _Module:
        if key not in modules:
            if is_file:
                modules[key] = _Module(key, key, "function")
            else:
                folder = key.endswith("/")
                name = posixpath.basename(key.rstrip("/"))
                modules[key] = _Module(key, name, "folder" if folder else "file")
        return modules[key]

    for function in tops:
        key = module_of(function.path, function.start)
        if key is None:
            continue
        found = module(key)
        found.files.add(function.path)
        found.functions += 1
        if is_file:
            found.start_line, found.end_line = function.start, function.end
            found.revision_id = function.revision_id
    for rule, rule_path, line in places:
        key = module_of(rule_path, line)
        if key is not None:
            holder = module(key)
            holder.files.add(rule_path)
            holder.rules.append(rule)
    weights: Counter[tuple[str, str]] = Counter()
    for edge in session.scalars(
        select(CodeEdge).where(
            CodeEdge.project_id == project_id, CodeEdge.to_symbol_id.is_not(None)
        )
    ):
        source = owner.get(edge.from_symbol_id)
        target = owner.get(edge.to_symbol_id) if edge.to_symbol_id else None
        if source is None or target is None:
            continue
        a = module_of(source.path, source.start)
        b = module_of(target.path, target.start)
        if a is not None and b is not None and a != b:
            weights[(a, b)] += 1
        elif a is not None and b is None:
            module(a).calls_out += 1
        elif b is not None and a is None:
            module(b).calls_in += 1
    for (a, b), count in weights.items():
        module(a).calls_out += count
        module(b).calls_in += count
    return {
        "path": path if is_file else prefix,
        "level": "file" if is_file else "folder",
        "modules": [
            {
                "key": m.key, "name": m.name, "kind": m.kind, "files": len(m.files),
                "functions": m.functions, "rules_count": len(m.rules),
                "rules": sorted(m.rules, key=lambda r: r["name"])[:MAX_RULES_LISTED],
                "calls_in": m.calls_in, "calls_out": m.calls_out,
                "start_line": m.start_line, "end_line": m.end_line,
                "revision_id": str(m.revision_id) if m.revision_id else None,
            }
            for m in sorted(modules.values(), key=lambda m: (m.kind != "folder", m.name))
        ],
        "dependencies": [
            {"from": a, "to": b, "calls": count}
            for (a, b), count in sorted(weights.items(), key=lambda item: -item[1])
        ],
    }  # fmt: skip
