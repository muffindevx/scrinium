"""Documents against the code (phase 8.2): «the policy says 50, the code decides with 40».

A sentence of a document (Markdown, text) that carries numbers is matched to a rule when they
share at least two meaningful words (title and statement, accents and case ignored). If none of
the sentence's numbers is a value the rule's decision decides with now (literals and same-file
constants resolved, a percentage also as a fraction or factor, as in `rule_checks`), the
document and the code disagree. A sentence with some of its numbers in the code is left alone:
it may simply say more than the rule.

No model and no network. It finds disagreements; which side is right is a person's call.

**Precision** (tuned on a real repository's 19 documents, where every finding was wrong):

- only prose is read: not tables, headings or code blocks, and not documents that are a run's
  output (a timestamp in the name, or under `results/`, `reports/`…);
- what only looks like a quantity is removed first: paths and routes, URLs, dates and times,
  versions, ticket keys, numbers with leading zeros (`0003`) or glued to letters (`qwen3`);
- like is compared with like: a rule that decides with numbers against the sentence's numbers,
  one that decides with names (`'succeeded'`) against its `names`;
- the rule's words come from its title and statement, not from identifiers it quotes
  (`MAX_BATCH_EVENTS` says nothing about batches), and a match needs a word of the title, or
  three shared words.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Final

from scrinium.application.analysis.rule_checks import unanchored_terms

MIN_SHARED: Final = 2
# Without any word of the rule's title, a sentence needs this many shared words to be about it.
MIN_SHARED_WITHOUT_TITLE: Final = 3
MAX_FINDINGS: Final = 200
# Documents that record a run rather than state a policy.
_RUN_OUTPUT: Final = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}|(?:^|/)(?:results?|reports?|outputs?|snapshots?|__snapshots__"
    r"|fixtures?|coverage)/|(?:^|/)changelog[^/]*$",
    re.IGNORECASE,
)
# What only looks like a quantity, removed before the numbers are read.
_NOT_QUANTITY: Final = re.compile(
    r"https?://\S+|\]\([^)]*\)|\S*/\S*"  # URLs, link targets, paths and routes
    r"|\b\d{4}-\d{2}-\d{2}\S*|\b\d{1,2}:\d{2}(?::\d{2})?\b"  # dates and times
    r"|\bv\d+(?:\.\d+)*\b|\b\d+\.\d+\.\d+\S*|@\d+"  # versions
    r"|\b[A-Z][A-Z0-9]*-\d+\b|§\s*\d+"  # ticket keys, sections
    r"|\b0\d+\b"  # identifiers with leading zeros
    r"|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"  # commit hashes
    r"|\b[A-Za-z_]+\d+\w*\b|\b\d+[A-Za-z_]+\w*\b"  # numbers glued to letters
)
# Identifiers a statement quotes: their words are the code's, not the rule's subject.
_IDENTIFIER: Final = re.compile(
    r"`[^`]*`|'[^'\s]*'|\"[^\"\s]*\"|\b[A-Za-z]+(?:_[A-Za-z0-9]+)+\b|\b[a-z]+[A-Z]\w*\b"
)
_NUMERIC: Final = re.compile(r"^-?\d+(?:\.\d+)?$")
_SENTENCE: Final = re.compile(r"(?<=[.!?;])\s+")
_WORD: Final = re.compile(r"[a-z]{4,}")
# A number, or a `name` (a rule may decide with names: statuses, roles, codes).
_HAS_NUMBER: Final = re.compile(r"\d|`[A-Za-z_]")
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


def is_run_output(path: str) -> bool:
    return _RUN_OUTPUT.search(path) is not None


def _sentences(text: str) -> list[tuple[int, str]]:
    """(line, sentence) of each prose sentence with a number or a `name`. A paragraph wrapped
    over several lines is joined first, and keeps its first line. Tables, headings, code blocks
    and HTML comments are not prose: their numbers are data, titles or examples."""
    found = []
    paragraph: list[str] = []
    first = 0
    fenced = False
    for number, raw in enumerate([*text.splitlines(), ""], 1):
        line = raw.strip()
        if line.startswith(("```", "~~~")):
            fenced = not fenced
            line = ""
        elif fenced or line.startswith(("|", "#", "<!--", "[")) or raw.startswith("    "):
            # A table row, a heading, a comment, a link definition or an indented code block
            # ends the paragraph and is not read.
            line = ""
        starts_item = line[:1] in "-*>" or line[:2].rstrip(".").isdigit()
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


def _comparable(sentence: str, rule: KnownValues) -> tuple[list[str], list[str]]:
    """(what the sentence says, what of it the code does not hold), of the same kind as the
    rule's values: numbers against a rule that decides with numbers, `names` against one that
    decides with names."""
    cleaned = _NOT_QUANTITY.sub(" ", sentence)
    numeric = any(_NUMERIC.match(value) for value in rule.values)
    names = " ".join(value for value in rule.values if not _NUMERIC.match(value))
    says = unanchored_terms(cleaned, (), "")  # every number and `name` of the sentence
    missing = unanchored_terms(cleaned, rule.values, f"{rule.code}\n{names}")

    def same_kind(term: str) -> bool:
        is_number = term[:1].isdigit()
        return is_number if numeric else not is_number and bool(names)

    return [t for t in says if same_kind(t)], [t for t in missing if same_kind(t)]


def _about(words: set[str], rule_words: set[str], title_words: set[str]) -> int:
    """How many words the sentence shares with the rule; 0 when that is not enough."""
    shared = len(words & rule_words)
    if shared < MIN_SHARED or (not words & title_words and shared < MIN_SHARED_WITHOUT_TITLE):
        return 0
    return shared


def doc_conflicts(rules: list[KnownValues], documents: list[tuple[str, str]]) -> list[DocConflict]:
    vocab = [
        (rule, _words(f"{rule.title} {_IDENTIFIER.sub(' ', rule.statement)}"), _words(rule.title))
        for rule in rules
        if rule.values
    ]
    conflicts = []
    for path, text in documents:
        if is_run_output(path):
            continue
        for line, sentence in _sentences(text):
            words = _words(_NOT_QUANTITY.sub(" ", sentence))
            scored = sorted(
                ((_about(words, known, title), rule) for rule, known, title in vocab),
                key=lambda pair: -pair[0],
            )
            if not scored or scored[0][0] == 0:
                continue
            best = scored[0][0]
            for shared, rule in scored:
                if shared < best:
                    break
                says, missing = _comparable(sentence, rule)
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
