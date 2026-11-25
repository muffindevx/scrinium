"""The small, strict subset of YAML that Scrinium's repository files use (ADR-012).

Rule files (`.scrinium/rules/`) and the product manifest (`scrinium.product.yaml`) are read by
people in pull requests and by `scrinium check` in CI, which must not depend on a YAML library
or guess. So the subset is:

- `key: value` at the top level, with scalars as JSON strings (valid YAML), bare words
  (`verified`, `guard`), `null`, `[]`, or `[start, end]` for line ranges;
- `key:` followed by an indented block that is either a list of flat mappings (`  - a: 1` and
  `    b: 2`) or one flat mapping (`  a: 1`);
- comments and blank lines.

Anything else is an error that names its line. Scalars stay text: a fingerprint such as `0789`
or `1e5` is never turned into a number.
"""

import json
import re
from pathlib import Path
from typing import Final

from scrinium.core.language import pick

Scalar = str | None
Fields = dict[str, tuple[int, Scalar]]
Value = Scalar | list[Fields] | Fields
Entries = dict[str, tuple[int, Value]]

_KEY: Final = re.compile(r"^([a-z_]+):(?: (.*))?$")
_PLAIN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$|^[0-9a-f]{8}-[0-9a-f-]{27}$")
_NUMBER_LIKE: Final = re.compile(r"^[-+]?[0-9.]+(?:e[-+]?[0-9]+)?$", re.I)
LINES: Final = re.compile(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]")


class FileFormatError(ValueError):
    def __init__(self, path: Path | str, line: int, message: str) -> None:
        super().__init__(f"{path}:{line}: {message}")
        self.path, self.line, self.message = str(path), line, message


# --- Writing ----------------------------------------------------------------------------------


def scalar(value: str | None) -> str:
    """A string in JSON quoting (valid YAML), or null."""
    return "null" if value is None else json.dumps(value, ensure_ascii=False)


def bare(value: str) -> str:
    """Kinds, statuses and ids: plain when no YAML reader can take them for something else."""
    plain = _PLAIN.match(value) and not _NUMBER_LIKE.match(value)
    return value if plain and value not in {"null", "true", "false"} else scalar(value)


# --- Reading ----------------------------------------------------------------------------------


def _scalar(raw: str | None, where: Path | str, number: int) -> Scalar:
    if raw is None or raw in {"", "null", "~"}:
        return None
    if raw.startswith('"'):
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            raise FileFormatError(
                where, number, pick("cadena entre comillas mal cerrada", "unclosed quoted string")
            ) from None
        if not isinstance(loaded, str):
            raise FileFormatError(
                where, number, pick("se esperaba una cadena", "a string was expected")
            )
        return loaded
    if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
        return raw[1:-1].replace("''", "'")
    if LINES.fullmatch(raw):
        return raw
    if raw.startswith(("[", "{", "&", "*", "!", "|", ">")) and raw != "[]":
        raise FileFormatError(where, number, pick("valor no admitido", "unsupported value"))
    return raw


def parse(lines: list[str], where: Path | str, first_number: int = 1) -> Entries:
    """The entries of `lines` (the header of a rule file, or a whole manifest)."""
    entries: Entries = {}
    block: str | None = None
    for index, line in enumerate(lines):
        number = first_number + index
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" "):
            match = _KEY.match(stripped)
            if match is None:
                raise FileFormatError(
                    where, number, pick("se esperaba «clave: valor»", "«key: value» was expected")
                )
            key, raw = match[1], match[2]
            if key in entries:
                raise FileFormatError(where, number, pick(f"«{key}» repetida", f"«{key}» repeated"))
            if raw in (None, ""):
                block = key
                entries[key] = (number, None)
            else:
                block = None
                value: Value = [] if raw == "[]" else _scalar(raw, where, number)
                entries[key] = (number, value)
            continue
        if block is None:
            raise FileFormatError(
                where, number, pick("sangría inesperada", "unexpected indentation")
            )
        at, current = entries[block]
        item = stripped
        if item.startswith("- "):
            if isinstance(current, dict):
                raise FileFormatError(
                    where,
                    number,
                    pick(f"«{block}» mezcla lista y mapa", f"«{block}» mixes a list and a map"),
                )
            items = current if isinstance(current, list) else []
            items.append({})
            entries[block] = (at, items)
            current = items
            item = item[2:].strip()
        elif current is None:
            current = {}
            entries[block] = (at, current)
        match = _KEY.match(item)
        if match is None:
            raise FileFormatError(
                where, number, pick("se esperaba «clave: valor»", "«key: value» was expected")
            )
        target = current[-1] if isinstance(current, list) else current
        if not isinstance(target, dict):
            raise FileFormatError(
                where, number, pick(f"«{block}» mal formada", f"«{block}» is malformed")
            )
        target[match[1]] = (number, _scalar(match[2], where, number))
    return entries


def text_of(value: Value, where: Path | str, number: int, key: str) -> str:
    if not isinstance(value, str) or value == "":
        raise FileFormatError(where, number, pick(f"falta «{key}»", f"«{key}» is missing"))
    return value


def optional(entries: Entries | Fields, key: str) -> Scalar:
    value = entries.get(key, (0, None))[1]
    return value if isinstance(value, str) else None


def items(entries: Entries, key: str, where: Path | str) -> list[Fields]:
    at, value = entries.get(key, (0, []))
    if value is None:
        return []
    if not isinstance(value, list):
        raise FileFormatError(
            where, at, pick(f"«{key}» debe ser una lista", f"«{key}» must be a list")
        )
    return value


def mapping(entries: Entries, key: str, where: Path | str) -> Fields:
    at, value = entries.get(key, (0, None))
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise FileFormatError(
            where, at, pick(f"«{key}» debe ser un mapa", f"«{key}» must be a map")
        )
    return value
