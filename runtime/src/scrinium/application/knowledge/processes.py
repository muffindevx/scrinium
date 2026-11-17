"""Processes (phase 8.3): from an entry point, the rules a run goes through, in order.

No model finds them. An **entry point** is a function the outside world calls: a button or
action handler (`action_*`, Odoo's convention and many others'), a scheduled job (`cron`), or
an HTTP route (a decorator with `route`, `get`, `post`… just above it). From each one the
resolved call graph (`code_edges`) is walked depth-first, **in the order the calls appear in
the function's text**, and every function on the way that has rules cited inside it becomes a
step. A process with no step with rules is not shown: it says nothing about the business.

Its name is the entry point's, made readable («SaleOrder · confirm»); a model may later give
it a better one, but never decides what the process is.
"""

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.domain.enums import (
    EntityLifecycle,
    EntityType,
    EvidenceType,
    ItemLifecycle,
    VerificationStatus,
)
from scrinium.domain.paths import is_test_path
from scrinium.infrastructure.sqlite.models import (
    CodeEdge,
    CodeSymbol,
    EvidenceRecord,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    SourceConnection,
    SourceItem,
    SourceRevision,
)

MAX_DEPTH: Final = 5
MAX_STEPS: Final = 40
_ENTRY_NAME: Final = re.compile(r"^(action_\w+|_?cron_\w+|\w+_cron|handle_\w+)$")
_ROUTE: Final = re.compile(r"@\S*\b(route|get|post|put|patch|delete|api_view)\b")


@dataclass
class _Symbol:
    id: uuid.UUID
    name: str
    qualified: str
    path: str
    start: int
    end: int
    text: str
    rules: list[dict[str, Any]] = field(default_factory=list)


def _readable(qualified: str) -> str:
    owner, _, name = qualified.rpartition(".")
    words = re.sub(r"^_?(action|cron|handle)_|_cron$", "", name).replace("_", " ").strip()
    return f"{owner} · {words or name}" if owner else words or name


def _load(
    session: Session, project_id: uuid.UUID
) -> tuple[dict[uuid.UUID, _Symbol], dict[uuid.UUID, list[uuid.UUID]]]:
    symbols: dict[uuid.UUID, _Symbol] = {}
    lines_of: dict[uuid.UUID, list[str]] = {}
    for symbol, path, text in session.execute(
        select(CodeSymbol, SourceItem.provider_item_id, SourceRevision.text_content)
        .join(SourceItem, SourceItem.current_revision_id == CodeSymbol.source_revision_id)
        .join(SourceRevision, SourceRevision.id == CodeSymbol.source_revision_id)
        .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
        .where(
            SourceConnection.project_id == project_id,
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
            CodeSymbol.kind != "class",
        )
    ):
        if is_test_path(path):
            continue
        if symbol.source_revision_id not in lines_of:
            lines_of[symbol.source_revision_id] = (text or "").splitlines()
        lines = lines_of[symbol.source_revision_id]
        # One line above too: where a route decorator sits.
        body = "\n".join(lines[max(symbol.start_line - 2, 0) : symbol.end_line])
        symbols[symbol.id] = _Symbol(
            symbol.id, symbol.name, symbol.qualified_name, path, symbol.start_line,
            symbol.end_line, body,
        )  # fmt: skip
    calls: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    callee_name: dict[tuple[uuid.UUID, uuid.UUID], str] = {}
    for edge in session.scalars(
        select(CodeEdge).where(
            CodeEdge.project_id == project_id,
            CodeEdge.status == "resolved",
            CodeEdge.to_symbol_id.is_not(None),
        )
    ):
        if edge.from_symbol_id in symbols and edge.to_symbol_id in symbols:
            calls[edge.from_symbol_id].append(edge.to_symbol_id)
            callee_name[(edge.from_symbol_id, edge.to_symbol_id)] = edge.callee
    for caller, callees in calls.items():
        text = symbols[caller].text

        def position(callee: uuid.UUID, caller: uuid.UUID = caller, text: str = text) -> int:
            name = callee_name[(caller, callee)].rsplit(".", 1)[-1]
            found = re.search(rf"\b{re.escape(name)}\s*\(", text)
            return found.start() if found else len(text)

        calls[caller] = sorted(dict.fromkeys(callees), key=position)
    _attach_rules(session, project_id, symbols)
    return symbols, calls


def _attach_rules(
    session: Session, project_id: uuid.UUID, symbols: dict[uuid.UUID, _Symbol]
) -> None:
    by_path: dict[str, list[_Symbol]] = defaultdict(list)
    for symbol in symbols.values():
        by_path[symbol.path].append(symbol)
    rows = session.execute(
        select(
            EvidenceRecord.locator,
            KnowledgeEntity.id,
            KnowledgeEntity.name,
            KnowledgeEntityVersion.verification_status,
        )
        .join(KnowledgeEntityVersion, KnowledgeEntityVersion.id == EvidenceRecord.target_version_id)
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
    for locator, entity_id, name, status in rows:
        locator = locator or {}
        if "start_line" not in locator:
            continue
        start = int(locator["start_line"])
        end = int(locator.get("end_line", start))
        holders = [s for s in by_path.get(str(locator.get("path", "")), [])
                   if s.start <= start and s.end >= end]  # fmt: skip
        if not holders:
            continue
        holder = min(holders, key=lambda s: s.end - s.start)
        if all(rule["id"] != str(entity_id) for rule in holder.rules):
            verified = status is VerificationStatus.HUMAN_VERIFIED
            holder.rules.append({"id": str(entity_id), "title": name, "verified": verified})


def _is_entry(symbol: _Symbol) -> bool:
    if _ENTRY_NAME.match(symbol.name):
        return True
    first = symbol.text.split("\n", 1)[0]
    return bool(_ROUTE.search(first))


def _walk(
    symbols: dict[uuid.UUID, _Symbol],
    calls: dict[uuid.UUID, list[uuid.UUID]],
    symbol_id: uuid.UUID,
    depth: int,
    seen: set[uuid.UUID],
    steps: list[dict[str, Any]],
) -> None:
    """Depth first, in call order: each function with rules on the way is a step."""
    if symbol_id in seen or len(steps) >= MAX_STEPS:
        return
    seen.add(symbol_id)
    symbol = symbols[symbol_id]
    if symbol.rules:
        steps.append({"function": symbol.qualified, "path": symbol.path,
                      "start_line": symbol.start, "end_line": symbol.end,
                      "depth": depth, "rules": symbol.rules})  # fmt: skip
    if depth < MAX_DEPTH:
        for callee in calls.get(symbol_id, []):
            _walk(symbols, calls, callee, depth + 1, seen, steps)


def processes(session: Session, project_id: uuid.UUID) -> list[dict[str, Any]]:
    symbols, calls = _load(session, project_id)
    found: list[dict[str, Any]] = []
    for entry in sorted(symbols.values(), key=lambda s: (s.path, s.start)):
        if not _is_entry(entry):
            continue
        steps: list[dict[str, Any]] = []
        _walk(symbols, calls, entry.id, 0, set(), steps)
        if steps:
            found.append({
                "name": _readable(entry.qualified), "entry": entry.qualified,
                "path": entry.path, "start_line": entry.start, "steps": steps,
                "rules": len({rule["id"] for step in steps for rule in step["rules"]}),
            })  # fmt: skip
    found.sort(key=lambda process: (-int(process["rules"]), str(process["name"])))
    return found


def processes_by_rule(found: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Each rule's processes, by name (phase 8.4: what a changed rule touches downstream)."""
    names: dict[str, list[str]] = defaultdict(list)
    for process in found:
        for step in process["steps"]:
            for rule in step["rules"]:
                if process["name"] not in names[rule["id"]]:
                    names[rule["id"]].append(process["name"])
    return dict(names)
