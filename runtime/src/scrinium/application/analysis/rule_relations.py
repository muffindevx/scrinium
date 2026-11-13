"""Relationships between rules, from the code and without the model (stage 3, phase C).

Reasons, strongest first; a pair of rules gets only the strongest one:

- `duplicate`: the same decision, cited in two places (same code, ignoring whitespace);
- `same_function:<name>`: both live in the same function, one after the other (the nested one
  counts: a game wrapped in one closure would otherwise relate everything); in a function over
  150 lines, only if they are within 40 lines;
- `calls:<hops>`: A's function calls B's, directly or through up to 3 hops (`depends_on`,
  from A to B). Only for ordinary functions, never a huge wrapper; never through a utility
  called from more than 10 functions; the 6 closest per rule;
- business code relates rules through data: `constraint:<name>` (the same constraint in the
  schema and in a migration), `permission:<name>` (the same permission required in several
  places) and `same_data:<table>` (rules on the same table, as a chain in code order).

A shared named constant was tried and dropped: in a hand review, 4 of 8 were wrong or
useless (two mechanics can share a noise radius without relating). They are stored as
relationships with a `basis` and the citation of one of the rules as
evidence; after each extraction the ones with a basis are rebuilt, and a person's are kept.
"""

import logging
import re
import uuid
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from itertools import combinations, pairwise
from typing import Final

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from scrinium.application.knowledge.common import KnowledgeError
from scrinium.application.knowledge.entities import latest_version
from scrinium.application.knowledge.evidence import add_relationship
from scrinium.domain.enums import EntityType, EvidenceType, RelationType
from scrinium.infrastructure.code_structure import NESTED, all_functions, smallest_holding
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import (
    CodeEdge,
    CodeSymbol,
    EntityRelationship,
    EvidenceRecord,
    KnowledgeEntity,
    Project,
    SourceRevision,
)

logger = logging.getLogger("scrinium.relations")

MAX_HOPS: Final = 3
# Called from more functions than this, a function is a utility (see `_reached`).
_MAX_CALLERS: Final = 10
# The closest dependencies of a rule that are kept: beyond, a rule's list stops being read.
_MAX_DEPENDENCIES: Final = 6
# A function this long is a wrapper (a whole game in one closure): no call chains through it.
_WRAPPER_LINES: Final = 150
# A constraint or permission many rules share says little about how two of them relate.
_MAX_RULES_PER_NAME: Final = 6
_MIN_DUPLICATE_CHARS: Final = 15
# A function this long holds a whole program (a game in one closure): never "same function".
_HUGE_FUNCTION_LINES: Final = 500
# Two rules this close in a long function usually belong to the same decision.
_NEAR_LINES: Final = 40
# Business code relates its rules through data: a constraint declared in the schema and again
# in a migration, a permission required in several routes, rules on the same table.
_CONSTRAINT: Final = re.compile(
    r"\b(?:ck|uq|ux|fk|chk)_[a-z0-9_]+\b|\b[a-z][a-z0-9_]*_check\b|\b[a-z][a-z0-9_]*_unique\b"
)
_PERMISSION: Final = re.compile(r"""['"]([a-z][\w-]*:[a-z][\w-]*)['"]""")
# In SQL, after TABLE, FROM, INTO, UPDATE or JOIN; in code, only a table declaration
# (`pgTable('users'`, `grc.table('app_users'`): elsewhere those words are prose or methods.
_SQL_TABLE: Final = re.compile(
    r"""\b(?:table|from|into|update|join)\s+(?:"?[A-Za-z_]\w*"?\.)?"?([a-z][a-z0-9_]*)"?""",
    re.IGNORECASE,
)
_CODE_TABLE: Final = re.compile(r"""(?:pgTable|\.table|Table)\(\s*['"`]([a-z][a-z0-9_]*)['"`]""")
# How far back a citation looks for the table it belongs to (the CREATE TABLE around a CHECK).
_TABLE_LOOKBACK_LINES: Final = 60
_PRIORITY: Final = {
    "duplicate": 0, "constraint": 1, "same_function": 2, "calls": 3, "permission": 4,
    "same_data": 5,
}  # fmt: skip
_NOT_TABLES: Final = frozenset({"if", "not", "exists", "only", "set", "the", "select", "where"})


@dataclass
class _Rule:
    entity_id: uuid.UUID
    evidence_id: uuid.UUID
    functions: set[tuple[uuid.UUID, int, int, str]] = field(default_factory=set)
    symbols: set[uuid.UUID] = field(default_factory=set)
    texts: set[str] = field(default_factory=set)
    constraints: set[str] = field(default_factory=set)
    permissions: set[str] = field(default_factory=set)
    tables: set[str] = field(default_factory=set)
    first_line: int = 0
    first_path: str = ""


@dataclass(frozen=True)
class Relation:
    from_id: uuid.UUID
    to_id: uuid.UUID
    relation_type: RelationType
    basis: str
    evidence_id: uuid.UUID


def _rules(session: Session, project_id: uuid.UUID) -> list[_Rule]:
    """Each rule with the functions, symbols, names, tables and code its citations hold."""
    rules: list[_Rule] = []
    files: dict[uuid.UUID, tuple[list[str], list[tuple[str, int, int]]]] = {}
    for entity in session.scalars(
        select(KnowledgeEntity).where(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
        )
    ):
        version = latest_version(session, entity.id)
        if version is None:
            continue
        records = session.scalars(
            select(EvidenceRecord).where(
                EvidenceRecord.target_version_id == version.id,
                EvidenceRecord.source_revision_id.is_not(None),
                EvidenceRecord.evidence_type.in_((EvidenceType.CODE, EvidenceType.SCHEMA)),
            )
        ).all()
        rule: _Rule | None = None
        for record in records:
            locator = record.locator or {}
            if "start_line" not in locator or record.source_revision_id is None:
                continue
            start = int(locator["start_line"])
            end = int(locator.get("end_line", start))
            revision_id = record.source_revision_id
            if revision_id not in files:
                revision = session.get(SourceRevision, revision_id)
                text = (revision.text_content or "") if revision is not None else ""
                path = (revision.metadata_ or {}).get("path", "") if revision is not None else ""
                files[revision_id] = (text.split("\n"), all_functions(path, text))
            lines, functions = files[revision_id]
            rule = rule or _Rule(
                entity.id, record.id, first_line=start, first_path=str(locator.get("path", ""))
            )
            cited = "\n".join(lines[start - 1 : end])
            rule.constraints |= set(_CONSTRAINT.findall(cited))
            rule.permissions |= set(_PERMISSION.findall(cited))
            rule.tables |= _tables(str(locator.get("path", "")), lines, start, end)
            compact = re.sub(r"\s+", "", cited)
            if len(compact) >= _MIN_DUPLICATE_CHARS:
                rule.texts.add(compact)
            holder = smallest_holding(functions, start, end)
            if holder is not None:
                rule.functions.add((revision_id, holder[1], holder[2], holder[0]))
            symbol = session.scalars(
                select(CodeSymbol)
                .where(
                    CodeSymbol.source_revision_id == revision_id,
                    # Call chains only between top-level functions: between nested ones, a hand
                    # review found 20 of 20 wrong (closures of one game loop calling each other).
                    CodeSymbol.kind.not_in(("class", NESTED)),
                    CodeSymbol.start_line <= start,
                    CodeSymbol.end_line >= end,
                    CodeSymbol.end_line - CodeSymbol.start_line <= _WRAPPER_LINES,
                )
                .order_by(CodeSymbol.end_line - CodeSymbol.start_line)
                .limit(1)
            ).first()
            if symbol is not None:
                rule.symbols.add(symbol.id)
        if rule is not None:
            rules.append(rule)
    return rules


def _tables(path: str, lines: list[str], start: int, end: int) -> set[str]:
    """The tables a citation is about: named in its lines, or else the last one named before it
    (the CREATE TABLE or pgTable around a constraint, the SELECT in a trigger function)."""
    pattern = _SQL_TABLE if path.lower().endswith(".sql") else _CODE_TABLE

    def named(text: str) -> list[str]:
        return [t.lower() for t in pattern.findall(text) if t.lower() not in _NOT_TABLES]

    found = named("\n".join(lines[start - 1 : end]))
    if found:
        return set(found)
    before = named("\n".join(lines[max(0, start - 1 - _TABLE_LOOKBACK_LINES) : start - 1]))
    return {before[-1]} if before else set()


def _reached(session: Session, project_id: uuid.UUID) -> dict[uuid.UUID, dict[uuid.UUID, int]]:
    """For each function, the functions it reaches through calls, with the fewest hops."""
    calls: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for edge in session.scalars(
        select(CodeEdge).where(
            CodeEdge.project_id == project_id, CodeEdge.to_symbol_id.is_not(None)
        )
    ):
        if edge.to_symbol_id is not None:
            calls[edge.from_symbol_id].add(edge.to_symbol_id)
    # A utility many functions call (`tileAt`, `el`) connects everything to everything: chains
    # neither go through it nor end in it.
    callers = Counter(target for targets in calls.values() for target in targets)
    hubs = {target for target, count in callers.items() if count > _MAX_CALLERS}
    calls = {source: targets - hubs for source, targets in calls.items()}
    reached: dict[uuid.UUID, dict[uuid.UUID, int]] = {}
    for start in calls:
        hops = {start: 0}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            if hops[current] == MAX_HOPS:
                continue
            for target in calls.get(current, ()):
                if target not in hops:
                    hops[target] = hops[current] + 1
                    queue.append(target)
        del hops[start]
        reached[start] = hops
    return reached


def derive(session: Session, project_id: uuid.UUID) -> list[Relation]:
    """The relationships the code shows between the project's rules."""
    rules = _rules(session, project_id)
    best: dict[tuple[uuid.UUID, uuid.UUID], tuple[int, Relation]] = {}

    def offer(a: _Rule, b: _Rule, kind: str, detail: str, depends: bool = False) -> None:
        if a.entity_id == b.entity_id:
            return
        relation = Relation(
            a.entity_id, b.entity_id,
            RelationType.DEPENDS_ON if depends else RelationType.RELATED_TO,
            f"{kind}:{detail}" if detail else kind, a.evidence_id,
        )  # fmt: skip
        # One relationship per pair of rules, whatever its direction: the strongest reason.
        low, high = sorted((a.entity_id, b.entity_id), key=str)
        current = best.get((low, high))
        if current is None or _PRIORITY[kind] < current[0]:
            best[(low, high)] = (_PRIORITY[kind], relation)

    by_text: dict[str, list[_Rule]] = defaultdict(list)
    by_function: dict[tuple[uuid.UUID, int, int, str], list[_Rule]] = defaultdict(list)
    by_symbol: dict[uuid.UUID, list[_Rule]] = defaultdict(list)
    for rule in rules:
        for text in rule.texts:
            by_text[text].append(rule)
        for function in rule.functions:
            by_function[function].append(rule)
        for symbol in rule.symbols:
            by_symbol[symbol].append(rule)
    for group in by_text.values():
        for a, b in combinations(group, 2):
            offer(a, b, "duplicate", "")
    for (_, first, last, name), group in by_function.items():
        # Within a function each rule is linked to the next one in the code, not to all: a
        # chain, not a hairball. In a long function (a game's main loop) only if they are close.
        long = last - first > _WRAPPER_LINES
        if last - first > _HUGE_FUNCTION_LINES:
            # The whole program in one function: being in it says nothing.
            continue
        ordered = sorted(group, key=lambda rule: rule.first_line)
        for a, b in pairwise(ordered):
            if not long or b.first_line - a.first_line <= _NEAR_LINES:
                offer(a, b, "same_function", name)
    reached = _reached(session, project_id)
    dependencies: dict[uuid.UUID, list[tuple[int, _Rule, _Rule]]] = defaultdict(list)
    for symbol, holders in by_symbol.items():
        for target, hops in reached.get(symbol, {}).items():
            for a in holders:
                for b in by_symbol.get(target, []):
                    dependencies[a.entity_id].append((hops, a, b))
    for found in dependencies.values():
        closest = sorted(found, key=lambda item: (item[0], str(item[2].entity_id)))
        for hops, a, b in closest[:_MAX_DEPENDENCIES]:
            offer(a, b, "calls", str(hops), depends=True)
    for kind, attribute in (("constraint", "constraints"), ("permission", "permissions")):
        grouped: dict[str, list[_Rule]] = defaultdict(list)
        for rule in rules:
            for name in getattr(rule, attribute):
                grouped[name].append(rule)
        for name, group in grouped.items():
            if len(group) <= _MAX_RULES_PER_NAME:
                for a, b in combinations(group, 2):
                    offer(a, b, kind, name)
    # Rules on the same table: a chain in code order, not every pair.
    by_table: dict[str, list[_Rule]] = defaultdict(list)
    for rule in rules:
        for table in rule.tables:
            by_table[table].append(rule)
    for table, group in by_table.items():
        ordered = sorted(group, key=lambda rule: (rule.first_path, rule.first_line))
        for a, b in pairwise(ordered):
            offer(a, b, "same_data", table)
    return [relation for _, relation in best.values()]


def rebuild_relations(database: Database, project_id: uuid.UUID) -> int:
    """Replaces the project's code-derived relationships. Returns how many it stored."""
    with database.session() as session:
        project = session.get_one(Project, project_id)
        session.execute(
            delete(EntityRelationship).where(
                EntityRelationship.project_id == project_id,
                EntityRelationship.basis.is_not(None),
            )
        )
        stored = 0
        for relation in derive(session, project_id):
            try:
                relationship, created = add_relationship(
                    session, project, relation.from_id, relation.relation_type, relation.to_id,
                    evidence_id=relation.evidence_id, created_by="scrinium",
                )  # fmt: skip
            except KnowledgeError as error:
                # A call cycle (A reaches B and B reaches A) keeps only the first direction.
                logger.debug("relation_skipped code=%s", error.code)
                continue
            if created:
                relationship.basis = relation.basis[:200]
                stored += 1
        return stored
