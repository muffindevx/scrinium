"""Who calls whom (stage 3, phase B): each name a function calls, resolved to a function of the
same project, without the model.

Resolution order, for a name called from a file:

1. a function of that same file;
2. a function of the file it was imported from (relative imports and `@/` aliases in
   TypeScript and JavaScript, relative and absolute imports in Python);
3. the only function in the project with that name; with several, the one in the same
   folder, or else it stays `ambiguous`.

A name no function of the project has (`push`, `map`, a library's) is external: it is counted,
not stored. Tests are left out, as everywhere in the product. The table is derived: rebuilt
for the whole project after each sync.
"""

import posixpath
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Final

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from scrinium.application.analysis.candidates import is_rule_source
from scrinium.domain.enums import ItemLifecycle
from scrinium.domain.paths import is_test_path
from scrinium.infrastructure.code_structure import Import, parse_imports
from scrinium.infrastructure.sqlite.models import (
    CodeEdge,
    CodeSymbol,
    Project,
    SourceConnection,
    SourceItem,
    SourceRevision,
)

RESOLVED: Final = "resolved"
AMBIGUOUS: Final = "ambiguous"
EXTERNAL: Final = "external"

_JS_SUFFIXES: Final = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", "/index.ts",
                       "/index.tsx", "/index.js")  # fmt: skip
_PY_SUFFIXES: Final = (".py", "/__init__.py")
# Path aliases most TypeScript projects declare in tsconfig: `@/lib/api` is `src/lib/api`.
_ALIASES: Final = (("@/", "src/"), ("~/", "src/"))


@dataclass
class EdgeStats:
    calls: int = 0
    resolved: int = 0
    ambiguous: int = 0
    external: int = 0

    @property
    def internal_resolved(self) -> float | None:
        """Resolved calls among those to a name some project function has."""
        internal = self.resolved + self.ambiguous
        return round(self.resolved / internal, 3) if internal else None


@dataclass(frozen=True)
class _Symbol:
    id: uuid.UUID
    name: str
    kind: str
    path: str
    calls: tuple[str, ...]


def _current(
    session: Session, project_id: uuid.UUID
) -> dict[uuid.UUID, tuple[list[_Symbol], dict[str, str]]]:
    """Per source: its functions and methods in current revisions, and each file's text. Two
    sources are separate code bases (ADR-011, 1.5): nothing resolves across them."""
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
    sources: dict[uuid.UUID, tuple[list[_Symbol], dict[str, str]]] = defaultdict(lambda: ([], {}))
    for symbol, revision, source_id in rows:
        path = revision.metadata_.get("path", "")
        # Tests and tooling folders (.claude, .github...) are out of the product's map too.
        if not path or is_test_path(path) or not is_rule_source(path):
            continue
        symbols, texts = sources[source_id]
        symbols.append(_Symbol(symbol.id, symbol.name, symbol.kind, path, tuple(symbol.calls)))
        texts[path] = revision.text_content or ""
    return dict(sources)


def _module_file(module: str, importer: str, files: set[str]) -> str | None:
    """The project file an import points to, or None (a package, or not found)."""
    folder = posixpath.dirname(importer)
    if importer.endswith(".py"):
        dots = len(module) - len(module.lstrip("."))
        rest = module[dots:].replace(".", "/")
        if dots:
            base = folder
            for _ in range(dots - 1):
                base = posixpath.dirname(base)
            stem = posixpath.join(base, rest) if rest else base
            return next((stem + s for s in _PY_SUFFIXES if stem + s in files), None)
        tails = [rest + s for s in _PY_SUFFIXES]
        return next(
            (f for f in sorted(files) for t in tails if f == t or f.endswith("/" + t)), None
        )
    for alias, target in _ALIASES:
        if module.startswith(alias):
            module = target + module[len(alias) :]
            break
    else:
        if not module.startswith("."):
            return None
        module = posixpath.normpath(posixpath.join(folder, module))
    return next((module + s for s in _JS_SUFFIXES if module + s in files), None)


def build_edges(session: Session, project: Project) -> EdgeStats:
    """Rebuilds the project's call edges. Returns how many calls resolved."""
    session.execute(delete(CodeEdge).where(CodeEdge.project_id == project.id))
    stats = EdgeStats()
    for symbols, texts in _current(session, project.id).values():
        _build_source(session, project, symbols, texts, stats)
    return stats


def _build_source(
    session: Session,
    project: Project,
    symbols: list[_Symbol],
    texts: dict[str, str],
    stats: EdgeStats,
) -> None:
    files = set(texts)
    by_file: dict[str, dict[str, list[_Symbol]]] = defaultdict(lambda: defaultdict(list))
    by_name: dict[str, list[_Symbol]] = defaultdict(list)
    for symbol in symbols:
        by_file[symbol.path][symbol.name].append(symbol)
        by_name[symbol.name].append(symbol)
    imports: dict[str, list[tuple[Import, str | None]]] = {
        path: [(i, _module_file(i.module, path, files)) for i in parse_imports(path, text)]
        for path, text in texts.items()
    }
    for caller in symbols:
        for name in caller.calls:
            stats.calls += 1
            target, status = _resolve(caller, name, by_file, by_name, imports.get(caller.path, []))
            if status == EXTERNAL:
                stats.external += 1
                continue
            if status == RESOLVED:
                stats.resolved += 1
            else:
                stats.ambiguous += 1
            session.add(
                CodeEdge(
                    workspace_id=project.workspace_id, project_id=project.id,
                    from_symbol_id=caller.id, to_symbol_id=target.id if target else None,
                    callee=name[:300], status=status,
                )
            )  # fmt: skip


# Methods of arrays, sets, maps, strings, promises and events: `x.find()` is almost never a
# project function called `find`. Only an imported receiver or `this` resolves them.
_COMMON_METHODS: Final = frozenset(
    {"find", "map", "filter", "forEach", "reduce", "some", "every", "includes", "indexOf", "push",
     "pop", "shift", "unshift", "slice", "splice", "concat", "join", "sort", "reverse", "fill",
     "keys", "values", "entries", "add", "set", "get", "has", "delete", "clear", "size", "then",
     "catch", "finally", "split", "replace", "trim", "test", "exec", "match", "toString", "call",
     "apply", "bind", "emit", "on", "off", "once", "start", "stop", "reset", "update", "close",
     "open", "write", "read", "send", "append", "remove", "insert", "log", "error", "warn",
     "info", "debug", "parse", "stringify", "assign", "next", "resolve", "reject", "now", "min",
     "max", "floor", "ceil", "round", "abs", "random", "hypot", "sqrt", "pow", "from", "of",
     "isArray", "freeze", "create", "extend", "items", "copy", "format", "lower",
     "upper", "strip", "startswith", "endswith", "execute", "scalars", "scalar", "commit"}
)  # fmt: skip
_SELF: Final = frozenset({"this", "self", "cls"})


def _resolve(
    caller: _Symbol,
    callee: str,
    by_file: dict[str, dict[str, list[_Symbol]]],
    by_name: dict[str, list[_Symbol]],
    imports: list[tuple[Import, str | None]],
) -> tuple[_Symbol | None, str]:
    receiver, _, name = callee.rpartition(".")
    local = [s for s in by_file[caller.path].get(name, []) if s.id != caller.id]
    if receiver:
        # `usersRepository.findByEmail()`: the receiver was imported from a project file.
        for item, module_file in imports:
            if module_file is not None and item.name == receiver:
                found = by_file[module_file].get(name, [])
                if found:
                    return found[0], RESOLVED
        if receiver in _SELF and local:
            return local[0], RESOLVED
        if name in _COMMON_METHODS:
            return None, EXTERNAL
    if local:
        return local[0], RESOLVED
    if not receiver:
        for item, module_file in imports:
            if module_file is not None and item.name == name:
                wanted = name if item.original in {"*", "default"} else item.original
                found = by_file[module_file].get(wanted, [])
                if found:
                    return found[0], RESOLVED
    everywhere = [s for s in by_name.get(name, []) if s.id != caller.id]
    if not everywhere:
        return None, EXTERNAL
    if len(everywhere) == 1:
        return everywhere[0], RESOLVED
    folder = posixpath.dirname(caller.path)
    near = [s for s in everywhere if posixpath.dirname(s.path) == folder]
    if len({s.path for s in near}) == 1:
        return near[0], RESOLVED
    return None, AMBIGUOUS


def edge_counts(session: Session, project_id: uuid.UUID) -> Counter[str]:
    return Counter(
        session.scalars(select(CodeEdge.status).where(CodeEdge.project_id == project_id))
    )


def _symbol_row(symbol: CodeSymbol, revision: SourceRevision) -> dict[str, object]:
    return {
        "symbol_id": str(symbol.id), "name": symbol.qualified_name, "kind": symbol.kind,
        "path": revision.metadata_.get("path", ""), "revision_id": str(revision.id),
        "start_line": symbol.start_line, "end_line": symbol.end_line,
    }  # fmt: skip


def neighbours(session: Session, project_id: uuid.UUID, symbol_id: uuid.UUID) -> dict[str, object]:
    """The functions a function calls and those that call it, resolved; and how many of its
    calls stayed ambiguous. Raises LookupError if the function is not in the project."""
    edges = session.scalars(
        select(CodeEdge).where(
            CodeEdge.project_id == project_id,
            (CodeEdge.from_symbol_id == symbol_id) | (CodeEdge.to_symbol_id == symbol_id),
        )
    ).all()
    if (
        not edges
        and session.scalar(
            select(CodeSymbol.id)
            .join(SourceItem, SourceItem.current_revision_id == CodeSymbol.source_revision_id)
            .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
            .where(CodeSymbol.id == symbol_id, SourceConnection.project_id == project_id)
        )
        is None
    ):
        raise LookupError(symbol_id)
    wanted = {e.to_symbol_id for e in edges if e.from_symbol_id == symbol_id and e.to_symbol_id}
    wanted |= {e.from_symbol_id for e in edges if e.to_symbol_id == symbol_id}
    rows = {
        symbol.id: _symbol_row(symbol, revision)
        for symbol, revision in session.execute(
            select(CodeSymbol, SourceRevision)
            .join(SourceRevision, SourceRevision.id == CodeSymbol.source_revision_id)
            .where(CodeSymbol.id.in_(wanted))
        )
    }
    calls = [rows[e.to_symbol_id] for e in edges
             if e.from_symbol_id == symbol_id and e.to_symbol_id in rows]  # fmt: skip
    called_by = [rows[e.from_symbol_id] for e in edges
                 if e.to_symbol_id == symbol_id and e.from_symbol_id in rows]  # fmt: skip
    ambiguous = sum(1 for e in edges if e.from_symbol_id == symbol_id and e.status == AMBIGUOUS)
    by_name = lambda row: (row["path"], row["start_line"])  # noqa: E731
    return {
        "calls": sorted({r["symbol_id"]: r for r in calls}.values(), key=by_name),
        "called_by": sorted({r["symbol_id"]: r for r in called_by}.values(), key=by_name),
        "ambiguous_calls": ambiguous,
    }
