"""Rule files: `.scrinium/rules/<id>.md`, the verified rules a repository carries (ADR-012).

One file per verified rule. A front matter in a small, strict subset of YAML (scalars, and one
list of flat mappings for the anchors) and, after it, the statement:

    ---
    scrinium: 1
    id: 0193a5c2-...
    title: "Solo se borran pedidos en presupuesto o cancelados"
    status: verified
    verified_by: "Ana"
    verified_at: "2026-11-16T18:00:00Z"
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

Phase 2 writes and reads the part `scrinium check` needs; phase 3 completes the format (WHEN and
THEN, the reason, provenance and tombstones of rejected rules) without changing these keys.

The writer is deterministic: keys in a fixed order, strings as JSON strings (valid YAML; a
fingerprint is always quoted, so no YAML reader takes `0789` or `1e5` for a number), `\\n`
line ends and a final newline, so exporting twice gives the same bytes and diffs stay clean.
The reader accepts only that subset, plus comments and blank lines, and says on which line
anything else is. The files hold no source code: paths, lines and fingerprints only.
"""

import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from scrinium.infrastructure.decisions import DECISIONS_VERSION

FORMAT_VERSION: Final = 1
RULES_DIR: Final = Path(".scrinium") / "rules"
VERIFIED: Final = "verified"

_FINGERPRINT: Final = re.compile(r"^[0-9a-f]{1,1024}$")
_KEY: Final = re.compile(r"^([a-z_]+):(?: (.*))?$")
_PLAIN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$|^[0-9a-f]{8}-[0-9a-f-]{27}$")
_LINES: Final = re.compile(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]")
_NUMBER_LIKE: Final = re.compile(r"^[-+]?[0-9.]+(?:e[-+]?[0-9]+)?$", re.I)


class RuleFileError(ValueError):
    def __init__(self, path: Path | str, line: int, message: str) -> None:
        super().__init__(f"{path}:{line}: {message}")
        self.path, self.line, self.message = str(path), line, message


@dataclass(frozen=True)
class FileAnchor:
    """Where a verified rule is decided: a decision node, by its fingerprints (ADR-011)."""

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
    title: str
    statement: str
    status: str = VERIFIED
    verified_by: str | None = None
    verified_at: str | None = None
    anchors: tuple[FileAnchor, ...] = field(default_factory=tuple)

    @property
    def file_name(self) -> str:
        return f"{self.id}.md"


# --- Writing ----------------------------------------------------------------------------------


def _scalar(value: str | None) -> str:
    return "null" if value is None else json.dumps(value, ensure_ascii=False)


def _bare(value: str) -> str:
    """Kinds, statuses and ids: plain when no YAML reader can take them for something else."""
    plain = _PLAIN.match(value) and not _NUMBER_LIKE.match(value)
    return value if plain and value not in {"null", "true", "false"} else _scalar(value)


def render(rule: RuleFile) -> str:
    lines = [
        "---",
        f"scrinium: {FORMAT_VERSION}",
        f"id: {_bare(rule.id)}",
        f"title: {_scalar(rule.title)}",
        f"status: {_bare(rule.status)}",
        f"verified_by: {_scalar(rule.verified_by)}",
        f"verified_at: {_scalar(rule.verified_at)}",
        "anchors:" if rule.anchors else "anchors: []",
    ]
    ordered = sorted(rule.anchors, key=lambda a: (a.path, a.start_line, a.end_line, a.shape))
    for anchor in ordered:
        lines += [
            f"  - path: {_scalar(anchor.path)}",
            f"    symbol: {_scalar(anchor.symbol)}",
            f"    kind: {_bare(anchor.kind)}",
            f"    lines: [{anchor.start_line}, {anchor.end_line}]",
            f"    shape: {_scalar(anchor.shape)}",
            f"    value: {_scalar(anchor.value)}",
            f"    sketch: {_scalar(anchor.sketch)}",
            f"    decisions: {_scalar(anchor.decisions)}",
        ]
    lines.append("---")
    statement = "\n".join(line.rstrip() for line in rule.statement.strip().splitlines())
    return "\n".join(lines) + "\n" + statement + "\n"


# --- Reading ----------------------------------------------------------------------------------


def _value(raw: str | None, where: Path | str, number: int) -> str | None:
    """A scalar as text: numbers stay text (a fingerprint can be all digits)."""
    if raw is None or raw == "" or raw == "null" or raw == "~":
        return None
    if raw.startswith('"'):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            raise RuleFileError(where, number, "cadena entre comillas mal cerrada") from None
        if not isinstance(loaded, str):
            raise RuleFileError(where, number, "se esperaba una cadena")
        return loaded
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    if _LINES.fullmatch(raw):
        return raw
    if raw.startswith(("[", "{", "&", "*", "!", "|", ">")) and raw != "[]":
        raise RuleFileError(where, number, "valor no admitido en un archivo de reglas")
    return raw


def _text(value: str | None, where: Path | str, number: int, key: str) -> str:
    if value is None or str(value) == "":
        raise RuleFileError(where, number, f"falta «{key}»")
    return str(value)


def _anchor(fields: dict[str, tuple[int, str | None]], where: Path | str) -> FileAnchor:
    first = min((number for number, _ in fields.values()), default=0)

    def get(key: str) -> tuple[int, str | None]:
        return fields.get(key, (first, None))

    lines_at, lines = get("lines")
    match = _LINES.fullmatch(str(lines or ""))
    if match is None:
        raise RuleFileError(where, lines_at, "«lines» debe ser [inicio, fin], como [10, 12]")
    start, end = int(match[1]), int(match[2])
    if not 1 <= start <= end:
        raise RuleFileError(where, lines_at, "«lines» fuera de rango")
    values: dict[str, str] = {}
    for key in ("shape", "value"):
        at, raw = get(key)
        text = _text(raw, where, at, key)
        if not _FINGERPRINT.match(text):
            raise RuleFileError(where, at, f"«{key}» debe ser una huella hexadecimal")
        values[key] = text
    sketch_at, sketch = get("sketch")
    if sketch and not _FINGERPRINT.match(str(sketch)):
        raise RuleFileError(where, sketch_at, "«sketch» debe ser hexadecimal")
    symbol = get("symbol")[1]
    decisions = get("decisions")[1]
    return FileAnchor(
        path=_text(get("path")[1], where, get("path")[0], "path"),
        symbol=None if symbol is None else str(symbol),
        kind=_text(get("kind")[1], where, get("kind")[0], "kind"),
        start_line=start,
        end_line=end,
        shape=values["shape"],
        value=values["value"],
        sketch=str(sketch or ""),
        decisions=str(decisions if decisions is not None else "1"),
    )


def parse(text: str, where: Path | str = "<regla>") -> RuleFile:
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise RuleFileError(where, 1, "el archivo debe empezar con ---")
    try:
        close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise RuleFileError(where, 1, "falta el --- que cierra la cabecera") from None

    top: dict[str, tuple[int, str | None]] = {}
    anchors: list[dict[str, tuple[int, str | None]]] = []
    in_anchors = False
    for index in range(1, close):
        number, line = index + 1, lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" "):
            match = _KEY.match(stripped)
            if match is None:
                raise RuleFileError(where, number, "se esperaba «clave: valor»")
            key, raw = match[1], match[2]
            in_anchors = key == "anchors" and raw in (None, "")
            if key == "anchors" and raw not in (None, "", "[]"):
                raise RuleFileError(where, number, "«anchors» debe ser una lista")
            top[key] = (number, None if key == "anchors" else _value(raw, where, number))
            continue
        if not in_anchors:
            raise RuleFileError(where, number, "sangría inesperada")
        item = stripped
        if item.startswith("- "):
            anchors.append({})
            item = item[2:].strip()
        elif not anchors:
            raise RuleFileError(where, number, "cada ancla empieza con «- »")
        match = _KEY.match(item)
        if match is None:
            raise RuleFileError(where, number, "se esperaba «clave: valor» en el ancla")
        anchors[-1][match[1]] = (number, _value(match[2], where, number))

    version = top.get("scrinium", (1, None))
    if version[1] != str(FORMAT_VERSION):
        raise RuleFileError(where, version[0], f"versión de formato no admitida: {version[1]}")
    id_at, rule_id = top.get("id", (1, None))
    rule_id = _text(rule_id, where, id_at, "id")
    try:
        uuid.UUID(rule_id)
    except ValueError:
        raise RuleFileError(where, id_at, "«id» debe ser un UUID") from None
    title_at, title = top.get("title", (1, None))

    def optional(key: str) -> str | None:
        raw = top.get(key, (1, None))[1]
        return None if raw is None else str(raw)

    return RuleFile(
        id=rule_id,
        title=_text(title, where, title_at, "title"),
        statement="\n".join(lines[close + 1 :]).strip(),
        status=optional("status") or VERIFIED,
        verified_by=optional("verified_by"),
        verified_at=optional("verified_at"),
        anchors=tuple(_anchor(fields, where) for fields in anchors),
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
            raise RuleFileError(path, 1, f"el archivo debe llamarse {rule.file_name}")
        rules.append(rule)
    return rules


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
