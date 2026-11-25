"""The product manifest and the mirrors between its repositories (ADR-012, phase 3.4 and 3.5).

A product often decides the same rule twice: the interface stops a 30 % discount and the API
too. `scrinium.product.yaml`, kept in the backend repository (owner's decision), names the
product's sources and which verified rules are **mirrors** of each other:

    scrinium: 1
    product: "Tienda"
    sources:
      - name: "backend"
        path: "."
      - name: "ui"
        path: "../tienda-ui"
    mirrors:
      - rule: "0193a5c2-..."
        mirror: "0193a5c3-..."

Paths are relative to the manifest's folder (a sibling clone, or a subfolder of a monorepo).
Each source has its own `.scrinium/rules/`. A mirror's rules must both be in the product's
sources: a rule of another product is an error, never a silent link.

**Divergence** compares the values each side decides with now (literals and same-file
constants, resolved), found by re-anchoring every anchor in the code as it is: "the UI allows
30 %, the API 25 %". No model, no network, no database.
"""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from scrinium.application.check.rule_files import RULES_DIR as _RULES_DIR
from scrinium.application.check.rule_files import (
    VERIFIED,
    FileAnchor,
    RuleFile,
    RuleFileError,
    load,
)
from scrinium.application.check.yaml_subset import (
    FileFormatError,
    items,
    optional,
    parse,
    text_of,
)
from scrinium.application.knowledge.anchoring import OldAnchor, reanchor
from scrinium.infrastructure.decisions import (
    DECISIONS_VERSION,
    Decision,
    anchor_for,
    parse_decisions,
)

MANIFEST: Final = "scrinium.product.yaml"
RULES_DIR: Final = _RULES_DIR


@dataclass(frozen=True)
class ProductSource:
    name: str
    path: Path


@dataclass(frozen=True)
class Mirror:
    rule: str
    mirror: str
    line: int


@dataclass(frozen=True)
class Manifest:
    product: str
    folder: Path
    sources: tuple[ProductSource, ...]
    mirrors: tuple[Mirror, ...]


def load_manifest(folder: Path) -> Manifest | None:
    """The manifest in `folder`, or None if there is none. A broken one is an error."""
    path = folder / MANIFEST
    if not path.is_file():
        return None
    entries = parse(path.read_text(encoding="utf-8").splitlines(), path)
    if optional(entries, "scrinium") != "1":
        raise FileFormatError(
            path, entries.get("scrinium", (1, None))[0], "«scrinium: 1» is missing"
        )
    product = text_of(optional(entries, "product"), path, 1, "product")
    sources = []
    for fields in items(entries, "sources", path):
        at = min(number for number, _ in fields.values())
        name = text_of(optional(fields, "name"), path, at, "name")
        relative = text_of(optional(fields, "path"), path, at, "path")
        sources.append(ProductSource(name, (folder / relative).resolve()))
    if len({s.name for s in sources}) != len(sources):
        raise FileFormatError(path, entries["sources"][0], "two sources with the same name")
    mirrors = []
    for fields in items(entries, "mirrors", path):
        at = min(number for number, _ in fields.values())
        mirrors.append(
            Mirror(
                text_of(optional(fields, "rule"), path, at, "rule"),
                text_of(optional(fields, "mirror"), path, at, "mirror"),
                at,
            )
        )
    return Manifest(product, folder.resolve(), tuple(sources), tuple(mirrors))


@dataclass(frozen=True)
class Side:
    source: str
    rule: RuleFile
    # (path, first line) of each anchor found now, and the values it decides with.
    places: tuple[tuple[str, int], ...]
    values: tuple[str, ...]
    lost: int


@dataclass
class MirrorReport:
    product: str
    pairs: int = 0
    divergent: list[tuple[Side, Side]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.divergent or self.errors)


def _side(source: ProductSource, rule: RuleFile) -> Side:
    """Where the rule's decisions are in the code now, and their values."""
    parsed: dict[str, list[Decision]] = {}
    for anchor in rule.anchors:
        if anchor.path not in parsed:
            target = source.path / anchor.path
            text = target.read_text(encoding="utf-8") if target.is_file() else ""
            parsed[anchor.path] = parse_decisions(anchor.path, text)
    olds = []
    for number, anchor in enumerate(rule.anchors):
        current = anchor
        if anchor.decisions != DECISIONS_VERSION:
            found = anchor_for(parsed[anchor.path], anchor.start_line, anchor.end_line)
            if found is not None:
                current = FileAnchor(
                    anchor.path, found.symbol, found.kind, found.start_line, found.end_line,
                    found.shape, found.value, found.sketch,
                )  # fmt: skip
        olds.append(
            OldAnchor(
                number, current.path, current.symbol, current.kind, current.shape,
                current.value, current.sketch, current.start_line,
            )
        )  # fmt: skip
    matches = reanchor(olds, parsed)
    places: list[tuple[str, int]] = []
    values: list[str] = []
    for number in range(len(olds)):
        match = matches.get(number)
        if match is not None:
            places.append((match.path, match.decision.start_line))
            values += match.decision.literals
    return Side(source.name, rule, tuple(places), tuple(values), len(olds) - len(matches))


def compare_mirrors(manifest: Manifest) -> MirrorReport:
    report = MirrorReport(manifest.product)
    owners: dict[str, tuple[ProductSource, RuleFile]] = {}
    for source in manifest.sources:
        try:
            for rule in load(source.path / RULES_DIR):
                if rule.status == VERIFIED:
                    owners[rule.id] = (source, rule)
        except (RuleFileError, OSError) as error:
            report.errors.append(f"{source.name}: {error}")
    for mirror in manifest.mirrors:
        missing = [rule_id for rule_id in (mirror.rule, mirror.mirror) if rule_id not in owners]
        if missing:
            report.errors.append(
                f"{MANIFEST}:{mirror.line}: {', '.join(missing)} is not a verified rule of "
                f"any source of {manifest.product}"
            )
            continue
        report.pairs += 1
        first, second = (_side(*owners[mirror.rule]), _side(*owners[mirror.mirror]))
        if first.lost or second.lost:
            continue  # Its code moved or is gone: the rule watch and the check say so.
        if Counter(first.values) != Counter(second.values):
            report.divergent.append((first, second))
    return report


def describe(first: Side, second: Side) -> str:
    """ "the rule says 0.3 in ui and 0.25 in backend", with the places."""

    def shown(side: Side) -> str:
        values = ", ".join(side.values) or "no values"
        where = ", ".join(f"{path}:{line}" for path, line in side.places)
        return f"{values} in {side.source} ({where})"

    title = first.rule.title or first.rule.id
    return f'"{title}": {shown(first)}; {shown(second)}'
