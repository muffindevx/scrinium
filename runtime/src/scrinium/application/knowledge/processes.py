"""Processes (phase 8.3): from an entry point, the rules a run goes through, in order.

No model finds them. An **entry point** is a function the outside world calls: a button or
action handler (`action_*`, Odoo's convention and many others'), a scheduled job (`cron`), or
an HTTP route (a decorator with `route`, `get`, `post`… just above it). From each one the
resolved call graph (`code_edges`) is walked depth-first, **in the order the calls appear in
the function's text**, and every function on the way that has rules cited inside it becomes a
step. A process with no step with rules is not shown: it says nothing about the business.

Routes registered with a call, not a decorator (`new Hono().post("/", …)`, Express's
`router.get("/:id", …)`, Fastify's `app.put(…)`), are entry points too: each call is one, its
steps are the rules cited inside the call and the functions called from it, and its path takes
the prefix it is mounted with (`.route("/v1/events", eventsRoutes(…))`) when one is found.

Each process says what **triggers** it: an HTTP route (`POST /v1/events`), an action, a
scheduled job or a handler. Its name is the route, or the entry point's made readable
(«SaleOrder · confirm»); a model may later give it a better one, but never decides what the
process is.
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
# A decorator's method and path: `@app.post("/orders")`, `@router.get('/{id}')`.
_DECORATED: Final = re.compile(
    r"@\S*?\.?(route|get|post|put|patch|delete)\(\s*[\"']([^\"']*)[\"']", re.IGNORECASE
)
# A route registered with a call. The path must start with "/": `c.get("session")` is not one.
_CALLED: Final = re.compile(r"\.(get|post|put|patch|delete|all)\(\s*([\"'`])(/[^\"'`]*)\2")
# Where a group of routes is mounted: `.route("/v1/events", eventsRoutes(...))`, Express's
# `.use("/runs", runsRouter(...))`.
_MOUNT: Final = re.compile(r"\.(?:route|use|register)\(\s*[\"'`](/[^\"'`]*)[\"'`]\s*,\s*(\w+)\s*\(")
_SCRIPT: Final = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


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
    # The line `text` starts at (one above the function, where a decorator sits).
    first_line: int = 1


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
        first = max(symbol.start_line - 1, 1)
        body = "\n".join(lines[first - 1 : symbol.end_line])
        symbols[symbol.id] = _Symbol(
            symbol.id, symbol.name, symbol.qualified_name, path, symbol.start_line,
            symbol.end_line, body, first_line=first,
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
    for caller, callees in calls.items():
        for callee in callees:
            _CALLEE_NAMES[(caller, callee)] = callee_name[(caller, callee)].rsplit(".", 1)[-1]
    _attach_rules(session, project_id, symbols)
    return symbols, calls


# The name each call uses, to find where in its caller's text the call is.
_CALLEE_NAMES: dict[tuple[uuid.UUID, uuid.UUID], str] = {}


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
            KnowledgeEntity.summary,
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
    for locator, entity_id, name, statement, status in rows:
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
            holder.rules.append({
                "id": str(entity_id), "title": name, "statement": statement or "",
                "verified": verified, "start_line": start, "end_line": end,
            })  # fmt: skip


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


def _trigger(symbol: _Symbol) -> dict[str, str]:
    """What sets the process off, in words a person reads: kind and label."""
    first = symbol.text.split("\n", 1)[0]
    decorated = _DECORATED.search(first)
    if decorated is not None:
        method = decorated[1].upper()
        verb = "" if method == "ROUTE" else f"{method} "
        return {"kind": "http", "label": f"{verb}{decorated[2] or '/'}"}
    if symbol.name.startswith("action_"):
        return {"kind": "action", "label": _readable(symbol.qualified)}
    if "cron" in symbol.name:
        return {"kind": "schedule", "label": _readable(symbol.qualified)}
    return {"kind": "handler", "label": _readable(symbol.qualified)}


def _close(text: str, start: int) -> int:
    """The offset just after the parenthesis that closes the call opened at or after `start`.
    Strings are skipped; an unbalanced call ends at the text's end."""
    depth, index, quote = 0, text.find("(", start), ""
    while 0 <= index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


def _routes_called(
    symbols: dict[uuid.UUID, _Symbol], calls: dict[uuid.UUID, list[uuid.UUID]]
) -> list[dict[str, Any]]:
    """Routes registered with a call inside a script function: one process each."""
    mounted: dict[str, str] = {}
    for symbol in symbols.values():
        if symbol.path.endswith(_SCRIPT):
            for prefix, function in _MOUNT.findall(symbol.text):
                mounted.setdefault(function, prefix.rstrip("/"))
    found = []
    for symbol in sorted(symbols.values(), key=lambda s: (s.path, s.start)):
        if not symbol.path.endswith(_SCRIPT):
            continue
        for match in _CALLED.finditer(symbol.text):
            end = _close(symbol.text, match.start())
            first = symbol.first_line + symbol.text.count("\n", 0, match.start())
            last = symbol.first_line + symbol.text.count("\n", 0, end)
            span = symbol.text[match.start() : end]
            steps: list[dict[str, Any]] = []
            here = [r for r in symbol.rules if first <= r["start_line"] and r["end_line"] <= last]
            if here:
                steps.append({"function": symbol.qualified, "path": symbol.path,
                              "start_line": first, "end_line": last, "depth": 0,
                              "rules": here})  # fmt: skip
            seen = {symbol.id}
            for callee in calls.get(symbol.id, []):
                name = _CALLEE_NAMES.get((symbol.id, callee), "")
                if name and re.search(rf"\b{re.escape(name)}\s*\(", span):
                    _walk(symbols, calls, callee, 1, seen, steps)
            if not steps:
                continue
            path = f"{mounted.get(symbol.name, '')}{match[3].rstrip('/') or ''}" or "/"
            label = f"{match[1].upper()} {path}"
            found.append({
                "name": label, "entry": symbol.qualified,
                "trigger": {"kind": "http", "label": label},
                "path": symbol.path, "start_line": first, "steps": steps,
            })  # fmt: skip
    return found


def processes(session: Session, project_id: uuid.UUID) -> list[dict[str, Any]]:
    symbols, calls = _load(session, project_id)
    found: list[dict[str, Any]] = []
    for entry in sorted(symbols.values(), key=lambda s: (s.path, s.start)):
        if not _is_entry(entry):
            continue
        steps: list[dict[str, Any]] = []
        _walk(symbols, calls, entry.id, 0, set(), steps)
        if steps:
            trigger = _trigger(entry)
            http = trigger["kind"] == "http"
            found.append({
                "name": trigger["label"] if http else _readable(entry.qualified),
                "entry": entry.qualified, "trigger": trigger,
                "path": entry.path, "start_line": entry.start, "steps": steps,
            })  # fmt: skip
    found += _routes_called(symbols, calls)
    for process in found:
        rules = {rule["id"]: rule for step in process["steps"] for rule in step["rules"]}
        process["rules"] = len(rules)
        process["verified"] = sum(1 for rule in rules.values() if rule["verified"])
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
