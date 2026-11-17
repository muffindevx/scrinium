"""Documents against the code (phase 8.2): «the policy says 50, the code decides with 40».

A sentence of a document (Markdown, text) that carries numbers is matched to a rule when they
share at least two meaningful words (title and statement, accents and case ignored). If none of
the sentence's numbers is a value the rule's decision decides with now (literals and same-file
constants resolved, a percentage also as a fraction or factor, as in `rule_checks`), the
document and the code disagree. A sentence with some of its numbers in the code is left alone:
it may simply say more than the rule.

No model and no network. It finds disagreements; which side is right is a person's call.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Final

from scrinium.application.analysis.rule_checks import unanchored_terms

MIN_SHARED: Final = 2
MAX_FINDINGS: Final = 200
_SENTENCE: Final = re.compile(r"(?<=[.!?;])\s+")
_WORD: Final = re.compile(r"[a-z]{4,}")
_HAS_NUMBER: Final = re.compile(r"\d")
_STOP: Final = frozenset(
    {
        "para", "como", "cuando", "desde", "hasta", "pero", "sobre", "entre", "este", "esta",
        "estos", "estas", "esos", "esas", "todo", "todos", "toda", "todas", "solo", "tiene",
        "tienen", "puede", "pueden", "debe", "deben", "cada", "otro", "otra", "otros", "otras",
        "mismo", "misma", "that", "this", "with", "from", "when", "must", "have", "only", "each",
        "into", "than", "more", "less", "what", "which",
    }
)  # fmt: skip


@dataclass(frozen=True)
class KnownValues:
    rule_id: str
    title: str
    statement: str
    values: tuple[str, ...]
    place: str
    # The code of the function that holds the decision: a number written there also counts
    # (the threshold is the decision, the 2 % it charges is two lines below).
    code: str = ""


@dataclass(frozen=True)
class DocConflict:
    rule_id: str
    title: str
    document: str
    line: int
    sentence: str
    says: tuple[str, ...]
    code: tuple[str, ...]
    place: str


def _stem(word: str) -> str:
    """A rough singular: «ventas» and «venta», «pedidos» and «pedido» are the same word."""
    for ending in ("es", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return word[: -len(ending)]
    return word


def _words(text: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return {_stem(w) for w in _WORD.findall(plain) if w not in _STOP}


def _sentences(text: str) -> list[tuple[int, str]]:
    """(line, sentence) of each sentence with a number. A paragraph wrapped over several
    lines is joined first, and keeps its first line."""
    found = []
    paragraph: list[str] = []
    first = 0
    for number, raw in enumerate([*text.splitlines(), ""], 1):
        line = raw.strip()
        starts_item = line[:1] in "-*#>" or line[:2].rstrip(".").isdigit()
        if not line or (starts_item and paragraph):
            joined = " ".join(paragraph)
            for sentence in _SENTENCE.split(joined):
                sentence = sentence.strip("#>*- ").strip()
                if sentence and _HAS_NUMBER.search(sentence):
                    found.append((first, sentence[:400]))
            paragraph = []
        if line:
            if not paragraph:
                first = number
            paragraph.append(line)
    return found


def doc_conflicts(rules: list[KnownValues], documents: list[tuple[str, str]]) -> list[DocConflict]:
    vocab = [(rule, _words(f"{rule.title} {rule.statement}")) for rule in rules if rule.values]
    conflicts = []
    for path, text in documents:
        for line, sentence in _sentences(text):
            words = _words(sentence)
            scored = sorted(
                ((len(words & known), rule) for rule, known in vocab),
                key=lambda pair: -pair[0],
            )
            if not scored or scored[0][0] < MIN_SHARED:
                continue
            best = scored[0][0]
            for shared, rule in scored:
                if shared < best:
                    break
                says = unanchored_terms(sentence, (), "")  # every number of the sentence
                missing = unanchored_terms(sentence, rule.values, rule.code)
                if says and len(missing) == len(says):
                    conflicts.append(
                        DocConflict(
                            rule.rule_id,
                            rule.title,
                            path,
                            line,
                            sentence,
                            tuple(says),
                            rule.values,
                            rule.place,
                        )
                    )
                    break
            if len(conflicts) >= MAX_FINDINGS:
                return conflicts
    return conflicts


_DOCUMENTS: Final = (".md", ".markdown", ".txt", ".rst", ".adoc")
_NOT_CODE: Final = re.compile(
    r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|/\*[\s\S]*?\*/|"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'|`[^`]*`|#[^\n]*|//[^\n]*"
)


def project_conflicts(session: Any, project: Any) -> list[DocConflict]:
    """The project's documents against its rules' decisions in the code as synced now."""
    from sqlalchemy import select

    from scrinium.application.knowledge.anchoring import OldAnchor, reanchor
    from scrinium.application.knowledge.rulebook import known_rules
    from scrinium.domain.enums import ItemLifecycle
    from scrinium.infrastructure.decisions import parse_decisions
    from scrinium.infrastructure.sqlite.models import SourceConnection, SourceItem, SourceRevision

    current: dict[str, str] = {}
    documents: list[tuple[str, str]] = []
    for path, text in session.execute(
        select(SourceItem.provider_item_id, SourceRevision.text_content)
        .join(SourceRevision, SourceRevision.id == SourceItem.current_revision_id)
        .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
        .where(
            SourceConnection.project_id == project.id,
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
        )
    ):
        if path.lower().endswith(_DOCUMENTS):
            documents.append((path, text or ""))
        else:
            current[path] = text or ""
    rules = []
    parsed: dict[str, Any] = {}
    for source in session.scalars(
        select(SourceConnection).where(SourceConnection.project_id == project.id)
    ):
        for rule in known_rules(session, project, source):
            values: list[str] = []
            places = []
            code = ""
            for number, anchor in enumerate(rule.anchors):
                if anchor.path not in current:
                    continue
                if anchor.path not in parsed:
                    parsed[anchor.path] = parse_decisions(anchor.path, current[anchor.path])
                old = OldAnchor(
                    number, anchor.path, anchor.symbol, anchor.kind, anchor.shape, anchor.value,
                    anchor.sketch, anchor.start_line,
                )  # fmt: skip
                match = reanchor([old], {anchor.path: parsed[anchor.path]}).get(number)
                if match is not None:
                    values += match.decision.literals
                    places.append(f"{match.path}:{match.decision.start_line}")
                    code += enclosing_code(match.path, current[match.path], match.decision)
            if values:
                rules.append(
                    KnownValues(
                        rule.id,
                        rule.title,
                        _statement(session, rule.id),
                        tuple(values),
                        ", ".join(places),
                        code,
                    )
                )
    return doc_conflicts(rules, documents)


def _statement(session: Any, rule_id: str) -> str:
    import uuid

    from scrinium.application.knowledge.entities import latest_version
    from scrinium.infrastructure.sqlite.models import BusinessRuleVersion

    version = latest_version(session, uuid.UUID(rule_id))
    content = session.get(BusinessRuleVersion, version.id) if version is not None else None
    return content.statement if content is not None else ""


def enclosing_code(path: str, text: str, decision: Any) -> str:
    """The smallest function that holds the decision, or the decision's own lines."""
    from scrinium.infrastructure.code_structure import parse_symbols

    holders = [
        (symbol.start_line, symbol.end_line)
        for symbol in parse_symbols(path, text)
        if symbol.start_line <= decision.start_line and symbol.end_line >= decision.end_line
    ]
    start, end = (
        min(holders, key=lambda span: span[1] - span[0])
        if holders
        else (decision.start_line, decision.end_line)
    )
    code = "\n".join(text.splitlines()[start - 1 : end]) + "\n"
    # Only what the code computes with: docstrings, comments and strings say things too.
    return _NOT_CODE.sub(" ", code)
