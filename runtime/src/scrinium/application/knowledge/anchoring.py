"""Re-anchoring: which decision of the new code each old anchor is (ADR-011, phase 1.4).

Pure: no database. Given the anchors a rule's citations had and the decisions of the code now,
it says, for each anchor, the decision that continues it and how it was found, cheapest first:

1. **exact:** same path, same symbol and same shape;
2. **same_file:** the same shape elsewhere in the file (the function was renamed or split);
3. **moved:** the same shape in another file, only if that shape occurs once in the code given
   (a common shape alone says nothing);
4. **fuzzy:** a real edit in place: same path, kind and symbol, MinHash similarity at least 0.5
   (within one function there are few candidates), or 0.25 when it is the only free decision of
   that kind left in the function; without a symbol, at least 0.8;
5. **one to one:** a decision continues at most one anchor; ties go to the closest line, and
   fuzzy pairs are assigned greedily by similarity.

An anchor that matches nothing is gone: its rule's decision was removed. When the caller knows
a file was renamed (`renames`, old path to new), its anchors are looked for in the new path.
"""

from collections import Counter
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from typing import Any, Final

from scrinium.infrastructure.decisions import Decision, similarity

FUZZY_THRESHOLD: Final = 0.8
# Inside the same function there are few decisions of a kind: a lower bar is safe there.
FUZZY_SAME_SYMBOL_THRESHOLD: Final = 0.5
# The only decision of its kind left in the function: a short condition that gained a clause.
FUZZY_SOLE_CANDIDATE_THRESHOLD: Final = 0.25

EXACT: Final = "exact"
SAME_FILE: Final = "same_file"
MOVED: Final = "moved"
FUZZY: Final = "fuzzy"


@dataclass(frozen=True)
class OldAnchor:
    """An anchor as stored in a citation, with the caller's key to tell them apart."""

    key: Hashable
    path: str
    symbol: str | None
    kind: str
    shape: str
    value: str
    sketch: str
    start_line: int

    @classmethod
    def from_locator(cls, key: Hashable, locator: dict[str, Any]) -> "OldAnchor | None":
        anchor = locator.get("anchor")
        if not isinstance(anchor, dict) or not anchor.get("shape"):
            return None
        return cls(
            key=key,
            path=str(locator.get("path", "")),
            symbol=anchor.get("symbol"),
            kind=str(anchor.get("kind", "")),
            shape=str(anchor["shape"]),
            value=str(anchor.get("value", "")),
            sketch=str(anchor.get("sketch", "")),
            start_line=int(anchor.get("start_line", 0)),
        )


@dataclass(frozen=True)
class Match:
    path: str
    decision: Decision
    how: str

    def value_changed(self, old: OldAnchor) -> bool:
        return self.decision.value != old.value


def _key(path: str, decision: Decision) -> tuple[str, int, int, str]:
    return (path, decision.start_line, decision.end_line, decision.shape)


def reanchor(
    old: Iterable[OldAnchor],
    new_by_path: dict[str, list[Decision]],
    renames: dict[str, str] | None = None,
) -> dict[Hashable, Match]:
    renamed = renames or {}
    pending = sorted(old, key=lambda anchor: (anchor.path, anchor.start_line))

    def where(anchor: OldAnchor) -> str:
        return renamed.get(anchor.path, anchor.path)

    claimed: set[tuple[str, int, int, str]] = set()
    found: dict[Hashable, Match] = {}

    def free(path: str, decisions: list[Decision]) -> list[Decision]:
        return [d for d in decisions if _key(path, d) not in claimed]

    def take(anchor: OldAnchor, path: str, decision: Decision, how: str) -> None:
        claimed.add(_key(path, decision))
        found[anchor.key] = Match(path, decision, how)

    def closest(anchor: OldAnchor, decisions: list[Decision]) -> Decision:
        """The same value first (an untouched copy), then the nearest line."""
        return min(
            decisions,
            key=lambda d: (d.value != anchor.value, abs(d.start_line - anchor.start_line)),
        )

    # 1 and 2: in the same file, by shape.
    for how, same_symbol in ((EXACT, True), (SAME_FILE, False)):
        for anchor in pending:
            if anchor.key in found:
                continue
            path = where(anchor)
            candidates = [
                d
                for d in free(path, new_by_path.get(path, []))
                if d.shape == anchor.shape and (not same_symbol or d.symbol == anchor.symbol)
            ]
            if candidates:
                found_how = how if path == anchor.path else MOVED
                take(anchor, path, closest(anchor, candidates), found_how)

    # 3: moved to another file, when the shape is rare enough to mean something.
    occurrences = Counter(d.shape for decisions in new_by_path.values() for d in decisions)
    for anchor in pending:
        if anchor.key in found or occurrences[anchor.shape] != 1:
            continue
        for path, decisions in new_by_path.items():
            match = next((d for d in free(path, decisions) if d.shape == anchor.shape), None)
            if match is not None and path != where(anchor):
                take(anchor, path, match, MOVED)
                break

    # 4 and 5: edited in place, assigned one to one by similarity.
    pairs: list[tuple[float, int, OldAnchor, Decision]] = []
    for anchor in pending:
        if anchor.key in found or not anchor.sketch:
            continue
        candidates = [
            d
            for d in free(where(anchor), new_by_path.get(where(anchor), []))
            if d.kind == anchor.kind and (anchor.symbol is None or d.symbol == anchor.symbol)
        ]
        if anchor.symbol is None:
            bar = FUZZY_THRESHOLD
        elif len(candidates) == 1:
            bar = FUZZY_SOLE_CANDIDATE_THRESHOLD
        else:
            bar = FUZZY_SAME_SYMBOL_THRESHOLD
        for decision in candidates:
            score = similarity(anchor.sketch, decision.sketch)
            if score >= bar:
                distance = abs(decision.start_line - anchor.start_line)
                pairs.append((score, -distance, anchor, decision))
    for _, _, anchor, decision in sorted(pairs, key=lambda p: (p[0], p[1]), reverse=True):
        if anchor.key not in found and _key(where(anchor), decision) not in claimed:
            take(anchor, where(anchor), decision, FUZZY)
    return found
