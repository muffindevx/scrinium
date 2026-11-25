"""Rule files: `.scrinium/rules/<id>.md`, the rules a repository carries (ADR-012).

One file per rule a person reviewed. A verified rule:

    ---
    scrinium: 1
    id: 0193a5c2-...
    title: "Solo se borran pedidos en presupuesto o cancelados"
    status: verified
    verified_by: "Ana"
    verified_at: "2026-11-16T18:00:00Z"
    provenance:
      extractor: "rules-detail-1"
      model: "deepseek-chat"
    anchors:
      - path: "sale/models/sale_order.py"
        symbol: "SaleOrder._unlink_except_draft_or_cancel"
        kind: guard
        lines: [1493, 1494]
        shape: "5f1c..."
        value: "9a0e..."
        sketch: "1ed1..."
        decisions: "2"
    ---
    Solo se pueden borrar pedidos en presupuesto o cancelados.

    WHEN:
    - el pedido está enviado o confirmado
    THEN:
    - no se puede borrar

    WHY:
    Un pedido confirmado ya generó entregas o facturas.

A rejected rule leaves a **tombstone**: `status: rejected`, the reason and its anchors, with no
title and no text, so the same decision is not proposed again while its shape is the same.

The header is the strict YAML subset of `yaml_subset.py`. The writer is deterministic (keys in
a fixed order, anchors by path and line, `\\n` line ends, a final newline), so exporting twice,
or exporting what was imported, gives the same bytes. The files hold no source code: paths,
lines and fingerprints only. Older files without the phase 3 keys still read.
"""

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from scrinium.application.check.yaml_subset import (
    LINES,
    Entries,
    Fields,
    FileFormatError,
    bare,
    items,
    mapping,
    optional,
    scalar,
    text_of,
)
from scrinium.application.check.yaml_subset import parse as parse_entries
from scrinium.infrastructure.decisions import DECISIONS_VERSION

FORMAT_VERSION: Final = 1
RULES_DIR: Final = Path(".scrinium") / "rules"
VERIFIED: Final = "verified"
REJECTED: Final = "rejected"

_FINGERPRINT: Final = re.compile(r"^[0-9a-f]{1,1024}$")
_SECTIONS: Final = ("WHEN:", "THEN:", "WHY:")

RuleFileError = FileFormatError


@dataclass(frozen=True)
class FileAnchor:
    """Where a rule is decided: a decision node, by its fingerprints (ADR-011)."""

    path: str
    symbol: str | None
    kind: str
    start_line: int
    end_line: int
    shape: str
    value: str
    sketch: str = ""
    # The version of the decision extractor that computed the fingerprints.
    decisions: str = DECISIONS_VERSION


@dataclass(frozen=True)
class RuleFile:
    id: str
    title: str | None
    statement: str
    status: str = VERIFIED
    verified_by: str | None = None
    verified_at: str | None = None
    anchors: tuple[FileAnchor, ...] = field(default_factory=tuple)
    when: tuple[str, ...] = ()
    then: tuple[str, ...] = ()
    why: str | None = None
    # Where the reviewed text came from: the extractor and the model, when a model wrote it.
    extractor: str | None = None
    model: str | None = None
    # Why a person rejected it (tombstones only).
    reason: str | None = None

    @property
    def file_name(self) -> str:
        return f"{self.id}.md"

    @property
    def is_tombstone(self) -> bool:
        return self.status == REJECTED


# --- Writing ----------------------------------------------------------------------------------


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _body(rule: RuleFile) -> list[str]:
    if rule.is_tombstone:
        return []
    lines = [line.rstrip() for line in rule.statement.strip().splitlines()]
    if rule.when or rule.then:
        lines.append("")
    if rule.when:
        lines += ["WHEN:", *(f"- {_one_line(item)}" for item in rule.when)]
    if rule.then:
        lines += ["THEN:", *(f"- {_one_line(item)}" for item in rule.then)]
    if rule.why:
        lines += ["", "WHY:", *(line.rstrip() for line in rule.why.strip().splitlines())]
    return lines


def render(rule: RuleFile) -> str:
    lines = ["---", f"scrinium: {FORMAT_VERSION}", f"id: {bare(rule.id)}"]
    if not rule.is_tombstone:
        lines.append(f"title: {scalar(rule.title)}")
    lines.append(f"status: {bare(rule.status)}")
    if rule.is_tombstone:
        lines.append(f"reason: {scalar(rule.reason)}")
    lines += [
        f"verified_by: {scalar(rule.verified_by)}",
        f"verified_at: {scalar(rule.verified_at)}",
    ]
    if not rule.is_tombstone and (rule.extractor or rule.model):
        lines += [
            "provenance:",
            f"  extractor: {scalar(rule.extractor)}",
            f"  model: {scalar(rule.model)}",
        ]
    lines.append("anchors:" if rule.anchors else "anchors: []")
    ordered = sorted(rule.anchors, key=lambda a: (a.path, a.start_line, a.end_line, a.shape))
    for anchor in ordered:
        lines += [
            f"  - path: {scalar(anchor.path)}",
            f"    symbol: {scalar(anchor.symbol)}",
            f"    kind: {bare(anchor.kind)}",
            f"    lines: [{anchor.start_line}, {anchor.end_line}]",
            f"    shape: {scalar(anchor.shape)}",
            f"    value: {scalar(anchor.value)}",
            f"    sketch: {scalar(anchor.sketch)}",
            f"    decisions: {scalar(anchor.decisions)}",
        ]
    lines.append("---")
    return "\n".join([*lines, *_body(rule)]) + "\n"


# --- Reading ----------------------------------------------------------------------------------


def _anchor(fields: Fields, where: Path | str) -> FileAnchor:
    first = min((number for number, _ in fields.values()), default=0)

    def at(key: str) -> int:
        return fields.get(key, (first, None))[0]

    lines = optional(fields, "lines")
    match = LINES.fullmatch(lines or "")
    if match is None:
        raise RuleFileError(where, at("lines"), "«lines» must be [start, end], like [10, 12]")
    start, end = int(match[1]), int(match[2])
    if not 1 <= start <= end:
        raise RuleFileError(where, at("lines"), "«lines» out of range")
    values: dict[str, str] = {}
    for key in ("shape", "value"):
        text = text_of(optional(fields, key), where, at(key), key)
        if not _FINGERPRINT.match(text):
            raise RuleFileError(where, at(key), f"«{key}» must be a hexadecimal fingerprint")
        values[key] = text
    sketch = optional(fields, "sketch") or ""
    if sketch and not _FINGERPRINT.match(sketch):
        raise RuleFileError(where, at("sketch"), "«sketch» must be hexadecimal")
    return FileAnchor(
        path=text_of(optional(fields, "path"), where, at("path"), "path"),
        symbol=optional(fields, "symbol"),
        kind=text_of(optional(fields, "kind"), where, at("kind"), "kind"),
        start_line=start,
        end_line=end,
        shape=values["shape"],
        value=values["value"],
        sketch=sketch,
        decisions=optional(fields, "decisions") or "1",
    )


def _parse_body(lines: list[str]) -> tuple[str, tuple[str, ...], tuple[str, ...], str | None]:
    """(statement, WHEN items, THEN items, WHY) of the text after the header."""
    sections: dict[str, list[str]] = {"": [], "WHEN:": [], "THEN:": [], "WHY:": []}
    current = ""
    for line in lines:
        if line.strip() in _SECTIONS:
            current = line.strip()
            continue
        sections[current].append(line)

    def listed(name: str) -> tuple[str, ...]:
        return tuple(
            line.strip()[2:].strip()
            for line in sections[name]
            if line.strip().startswith("- ") and line.strip()[2:].strip()
        )

    why = "\n".join(sections["WHY:"]).strip() or None
    return "\n".join(sections[""]).strip(), listed("WHEN:"), listed("THEN:"), why


def parse(text: str, where: Path | str = "<regla>") -> RuleFile:
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise RuleFileError(where, 1, "the file must start with ---")
    try:
        close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise RuleFileError(where, 1, "the --- closing the header is missing") from None
    entries: Entries = parse_entries(lines[1:close], where, first_number=2)

    version_at = entries.get("scrinium", (1, None))[0]
    if optional(entries, "scrinium") != str(FORMAT_VERSION):
        raise RuleFileError(
            where, version_at, f"unsupported format version: {optional(entries, 'scrinium')}"
        )
    id_at = entries.get("id", (1, None))[0]
    rule_id = text_of(optional(entries, "id"), where, id_at, "id")
    try:
        uuid.UUID(rule_id)
    except ValueError:
        raise RuleFileError(where, id_at, "«id» must be a UUID") from None
    status = optional(entries, "status") or VERIFIED
    if status not in {VERIFIED, REJECTED}:
        raise RuleFileError(where, entries["status"][0], f"unknown status: {status}")
    anchors = tuple(_anchor(fields, where) for fields in items(entries, "anchors", where))
    provenance = mapping(entries, "provenance", where)
    if status == REJECTED:
        return RuleFile(
            id=rule_id, title=None, statement="", status=status,
            verified_by=optional(entries, "verified_by"),
            verified_at=optional(entries, "verified_at"),
            anchors=anchors, reason=optional(entries, "reason"),
        )  # fmt: skip
    statement, when, then, why = _parse_body(lines[close + 1 :])
    title_at = entries.get("title", (1, None))[0]
    return RuleFile(
        id=rule_id,
        title=text_of(optional(entries, "title"), where, title_at, "title"),
        statement=statement,
        status=status,
        verified_by=optional(entries, "verified_by"),
        verified_at=optional(entries, "verified_at"),
        anchors=anchors,
        when=when,
        then=then,
        why=why,
        extractor=optional(provenance, "extractor"),
        model=optional(provenance, "model"),
    )


_FILE_NAME: Final = re.compile(r"^[0-9a-fA-F-]{36}\.md$")


def rule_paths(directory: Path) -> Iterator[Path]:
    """The rule files of a folder, in name order. Other files (a README) are left alone."""
    if directory.is_dir():
        yield from sorted(p for p in directory.iterdir() if _FILE_NAME.match(p.name))


def load(directory: Path) -> list[RuleFile]:
    rules = []
    for path in rule_paths(directory):
        rule = parse(path.read_text(encoding="utf-8"), path)
        if path.name != rule.file_name:
            raise RuleFileError(path, 1, f"the file must be named {rule.file_name}")
        rules.append(rule)
    return rules


def pending(directory: Path, rules: list[RuleFile]) -> tuple[int, int]:
    """(rules whose file is missing or different, files of rules no longer in the list)."""
    expected = {rule.file_name: render(rule).encode("utf-8") for rule in rules}
    differ = sum(
        1
        for name, content in expected.items()
        if not (directory / name).is_file() or (directory / name).read_bytes() != content
    )
    extra = sum(1 for path in rule_paths(directory) if path.name not in expected)
    return differ, extra


def write_all(directory: Path, rules: list[RuleFile]) -> tuple[int, int, int]:
    """Writes the rules and removes rule files no longer in the list. Returns (written,
    unchanged, removed). A file whose bytes would not change is not touched."""
    directory.mkdir(parents=True, exist_ok=True)
    written = unchanged = 0
    keep = set()
    for rule in rules:
        target = directory / rule.file_name
        keep.add(target.name)
        content = render(rule).encode("utf-8")
        if target.is_file() and target.read_bytes() == content:
            unchanged += 1
            continue
        target.write_bytes(content)
        written += 1
    removed = 0
    for path in rule_paths(directory):
        if path.name not in keep:
            path.unlink()
            removed += 1
    return written, unchanged, removed
