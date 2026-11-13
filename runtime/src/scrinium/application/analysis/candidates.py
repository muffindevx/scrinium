"""Which code deserves the model's attention: candidates for business rules, without AI.

The unit is the function or method (structural map). A rule is a DECISION about the
domain state, whatever the domain (an invoice, a permission or a game): a
condition against a threshold, a cap, a probability, a formula with a named constant,
a rejection or a state change. The score counts those decision lines; business-app
vocabulary (money, permissions, errors) only describes, it does not decide: with it,
2 of every 3 rules in a game never reached the model.

- A chunk without any decision line is not a candidate.
- Penalties: more drawing or sound lines than decision lines, few decisions for its size, and
  a UI path or name.
- Bonus: a small function with one decision is often the whole rule.
- From a long function only windows around its decisions go: the model reads less and does
  not get lost in the drawing around them.

SQL and languages without a grammar have their chunks scored the same way.
"""

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.domain.enums import ItemLifecycle
from scrinium.domain.paths import is_test_path
from scrinium.infrastructure.code_structure import NESTED, language_for
from scrinium.infrastructure.sqlite.models import (
    CodeSymbol,
    ContentChunk,
    SourceConnection,
    SourceItem,
    SourceRevision,
)

CANDIDATES_VERSION: Final = "4"
DEFAULT_MIN_SCORE: Final = 2
# A longer function is split: the small model loses track with huge texts.
MAX_CANDIDATE_CHARS: Final = 6000

_I: Final = re.IGNORECASE
# (reason, pattern, weight). Each signal counts once, however often it appears.
_SIGNALS: Final = (
    (
        "condicion con constante",
        re.compile(r"(?:[<>]=?|[!=]==?)\s*-?\d+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\s*(?:[<>]=?|[!=]==?)"),
        2,
    ),
    (
        "error o rechazo",
        re.compile(
            r"\b(?:raise|throw|throws|panic!?|reject|abort|Err\(|errors\.New|HTTPException"
            r"|ValidationError|PermissionError|Forbidden|Unauthorized)\b"
        ),
        2,
    ),
    (
        "validacion o permiso",
        re.compile(
            r"\b(?:valid\w*|verif\w*|ensure\w*|require[sd]?|must|allow\w*|forbid\w*|deny\w*"
            r"|permi\w*|authori[sz]\w*|roles?|admin\w*|owner\w*|can[A-Z_]\w*|is[A-Z_]\w*"
            r"|has[A-Z_]\w*|valida\w*|permis\w*|autoriz\w*)\b",
            _I,
        ),
        1,
    ),
    (
        "limite",
        re.compile(
            r"\b(?:max\w*|min(?:imum|imo)?\w*|limit\w*|threshold\w*|quota\w*|attempts?"
            r"|retr(?:y|ies)|limite\w*|maximo\w*|minimo\w*|intentos?)\b",
            _I,
        ),
        1,
    ),
    (
        "dinero o porcentaje",
        re.compile(
            r"\b(?:price\w*|amount\w*|total\w*|subtotal\w*|tax\w*|vat|fees?|discount\w*"
            r"|rates?|percent\w*|cost\w*|balance\w*|currency|invoice\w*|payment\w*|refund\w*"
            r"|commission\w*|interest|precio\w*|importe\w*|impuesto\w*|iva|descuento\w*"
            r"|tarifa\w*|comision\w*|saldo\w*|factura\w*|pago\w*)\b",
            _I,
        ),
        2,
    ),
    # Bare fractions: drawing and animation code is full of them. They only add.
    ("fraccion", re.compile(r"\b0\.\d+\b"), 1),
    (
        "fecha o plazo",
        re.compile(
            r"\b(?:expir\w*|deadline\w*|due\w*|days?|hours?|age|birth\w*|period\w*"
            r"|vencim\w*|plazo\w*|dias?|horas?|edad)\b",
            _I,
        ),
        1,
    ),
    (
        "estado",
        re.compile(
            r"\b(?:status|state|stage|transition\w*|approv\w*|cancel\w*|reject\w*|pending"
            r"|blocked|locked|suspend\w*|estado\w*|aprobad\w*|cancelad\w*|bloquead\w*)\b",
            _I,
        ),
        1,
    ),
    ("constante de negocio", re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b"), 1),
)
# Rules come from code. Documentation, configuration and instructions for
# AI agents (skills, prompts) are not business rules: they are searched and asked about, but
# not extracted.
CODE_EXTENSIONS: Final = frozenset(
    {
        ".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".vue", ".svelte",
        ".java", ".kt", ".kts", ".scala", ".groovy", ".go", ".rs", ".rb", ".php", ".cs", ".fs",
        ".vb", ".swift", ".m", ".mm", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".dart", ".ex",
        ".exs", ".erl", ".clj", ".lua", ".pl", ".r", ".jl", ".sql", ".prisma", ".graphql",
    }
)  # fmt: skip
TOOLING_DIRS: Final = frozenset(
    {
        ".claude", ".cursor", ".github", ".gitlab", ".vscode", ".idea", ".devcontainer",
        ".husky", ".circleci", ".windsurf", ".continue", ".aider", ".codex", ".gemini",
    }
)  # fmt: skip


def is_rule_source(path: str) -> bool:
    """Whether a file is code or a schema by its extension; tests are filtered apart."""
    parts = path.split("/")
    if any(part in TOOLING_DIRS for part in parts[:-1]):
        return False
    name = parts[-1]
    dot = name.rfind(".")
    return dot > 0 and name[dot:].lower() in CODE_EXTENSIONS


# A constant: named (MAX_LIVES, TAX_RATE) or a number.
_CONST: Final = r"(?:\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\b[A-Z][A-Z0-9]{2,}\b|-?\b\d+(?:\.\d+)?\b)"
_CMP: Final = r"(?:[<>]=?|[!=]==?|<>)"
# Business code decides differently from a game (Q1): a guard on a state field or a failed
# check (`if (!user.isActive)`, `if (!isAllowedEmail(...))`), an equality with a status or role
# (`role === 'ADMIN'`, SQL `status <> 'DRAFT'`), a schema constraint (`check(`, `CHECK (`), a
# permission ('users:manage') and an HTTP refusal (`status(403)`). SQL keywords come in capitals.
_GUARD: Final = (
    r"(?i:\bif\b)[^\n]*!\s*(?:await\s+)?(?:\w+\.)*(?:is|has|can|should|valid|verify|allow)\w*\s*\("
    r"|(?i:\bif\b)[^\n]*\.(?:is|has|can|should)[A-Z]\w*"
    # A status or role is usually an upper-case value; a game's `kind === 'horde'` is not.
    r"""|(?:[!=]==?|<>|(?i:\bif\b)[^\n'"]*\s=)\s*['"][A-Z][A-Z0-9_]+['"]"""
    r"|(?i:\bcheck\b)\s*\("
    r"""|['"][a-z][\w-]*:[a-z][\w-]*['"]"""
    r"|\bstatus\(\s*4\d\d\b|\bstatus_code\s*=\s*4\d\d\b"
)
# A decision line, in any domain.
_DECISION: Final = re.compile(
    rf"{_GUARD}"
    rf"|(?i:\b(?:if|while|elif|elsif|case|when|unless)\b).*{_CMP}\s*{_CONST}|{_CONST}\s*{_CMP}"
    # Comparison between two values; with spaces, so it is not mistaken for generics or `=>`.
    r"|\s(?:<|>|<=|>=)\s"
    # Probability and caps.
    rf"|random\(\)\s*<|\b(?:Math\.)?(?:min|max|clamp)\([^)]*{_CONST}"
    # Rejection, and a predicate that returns a comparison.
    rf"|(?i:\b(?:throw|raise|abort)\b)|\breturn\b[^;\n]*{_CMP}"
    # Formula with a named constant.
    r"|[-+*/%]=?\s*\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b"
    # State change.
    r"""|\b(?:state|status|phase|stage|mode|lives)\s*=\s*['"\w]""",
)
# A threshold: a named constant or a number other than 0 or 1 (those are usually indexes,
# empty lists or counters, not rules).
_NAMED: Final = r"(?:\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\b[A-Z][A-Z0-9]{2,}\b)"
_THRESHOLD: Final = rf"(?:{_NAMED}|-?\b(?:[2-9]|\d{{2,}}|\d+\.\d+)\b)"
# P1: a domain decision, stricter than _DECISION (which decides which chunks pass the
# F10 filter): it is the one the model sees marked and answers.
_DOMAIN_DECISION: Final = re.compile(
    # A business guard, constraint or permission (Q1).
    rf"{_GUARD}"
    # Comparison against a threshold.
    rf"|{_CMP}\s*{_THRESHOLD}|{_THRESHOLD}\s*{_CMP}"
    # Probability and caps.
    rf"|random\(\)\s*<|\b(?:Math\.)?(?:min|max|clamp)\([^)]*{_THRESHOLD}"
    # Rechazo.
    r"|(?i:\b(?:throw|raise|abort)\b)"
    # Formula with a named constant.
    r"|[-+*/%]=?\s*\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b"
    # State change.
    r"""|\b(?:state|status|phase|stage|mode)\s*=\s*['"\w]"""
    # A quantity running out or crossing zero (lives <= 0), except indexes and sizes.
    r"|\b(?!length\b|size\b|index\b|[ijkn]\b)[A-Za-z_]\w*\s*(?:<=|>=|<|>)\s*0\b"
    # Equality with a named value ('decoy', "approved"): a state or a type.
    r"""|[!=]==?\s*['"]\w|['"]\w+['"]\s*[!=]==?"""
)
# Comparison between two values; with spaces, so it is not mistaken for generics or `=>`. It
# only counts if it touches an object field (state.raresLeft, character.unlockCost): between
# local variables (gap > bestGap, i < n) it is usually an implementation detail.
_LOCAL_COMPARISON: Final = re.compile(r"\s(?:<|>|<=|>=|===|!==|==|!=)\s")
_MEMBER: Final = re.compile(r"\b[a-z_]\w*\.(?!length\b|size\b|x\b|y\b)[a-z_]\w*")
_TRIVIAL_EQUALITY: Final = re.compile(r"[!=]==?\s*(?:0|1|null|undefined|None)\b")
# A presentation line: drawing, sound, DOM.
_VIEW: Final = re.compile(
    r"\.(?:fill|stroke|circle|rect|roundRect|moveTo|lineTo|arc|drawImage|fillText|addChild"
    r"|removeChild|beginPath|clear)\(|\b(?:ctx|graphics|gfx|audio|synth|sprite|PALETTE)\b\."
    r"|\.position\.set|\.scale\.set|\.(?:alpha|tint|visible|rotation|zIndex)\s*="
    r"|\bstyle\.|classList|innerHTML|textContent"
)
_COMMENT: Final = ("//", "#", "*", "/*", "--")
# A calculation with money or business quantities (a total, a tax) is also a rule,
# even without a constant: "the total is the sum of the lines".
_MONEY: Final = next(pattern for reason, pattern, _ in _SIGNALS if reason == "dinero o porcentaje")
_CALCULATION: Final = re.compile(r"[-+*/%]|\b(?:sum|round|ceil|floor|reduce)\(")
# UI, drawing, animation and sound rarely hold business rules.
_VISUAL_DIR: Final = re.compile(
    r"(?:^|/)(?:screens?|ui|components|widgets|sounds?|audio|music|sfx|fx|particles|sprites"
    r"|animations?|styles|themes|assets|graphics|gfx)/"
)
_VISUAL_NAME: Final = re.compile(
    r"hud|overlay|render|draw|paint|audio|sound|music|sfx|particle|sprite|animat|tween|decor"
    r"|theme|style|layout|touch|camera|smooth|graphic|gfx|icon|texture|font|colou?r|glow|pulse"
    r"|twinkle|flash|shake|fade|tint|splash|banner|tooltip|button|scroll|resize",
    _I,
)
_VISUAL_PENALTY: Final = 2
_MAX_DECISIONS: Final = 6
# Density: decisions per 10 lines, in chunks of at least this size.
_DENSITY_MIN_LINES: Final = 20
_LOW_DENSITY: Final = 0.5
_SMALL_FUNCTION_LINES: Final = 15
# A longer function only goes as windows of +-_WINDOW lines around its decisions.
_WINDOW_FROM_LINES: Final = 40
_WINDOW: Final = 6

# An explicit refusal, and logic that lives in the database (a function or a trigger): both are
# rules on their own, even with a single decision (Q1).
_REJECTION: Final = re.compile(
    r"(?i:\b(?:throw|raise)\b)|\babort\(|\bstatus\(\s*4\d\d\b|\bHTTPException\("
)
_DATABASE_LOGIC: Final = re.compile(
    r"(?i:\bcreate\s+(?:or\s+replace\s+)?(?:function|trigger|procedure)\b)"
)

_BRANCH: Final = re.compile(
    r"(?i:\b(?:if|elif|elsif|switch|case|when|match|unless)\b)|\?\s*[^:?\n]+:"
)


@dataclass
class Candidate:
    revision_id: uuid.UUID
    path: str
    start_line: int
    end_line: int
    text: str
    score: int
    reasons: list[str]
    # Function or method from the structural map; None for a chunk (SQL, other languages).
    symbol: str | None = None
    logic_hash: str | None = None
    # Chunk number within its function, if it was split for length.
    part: int = 0


@dataclass
class Selection:
    candidates: list[Candidate] = field(default_factory=list)
    functions_total: int = 0
    below_threshold: int = 0
    not_code: int = 0


def _code_lines(text: str) -> list[str]:
    return [
        line for line in text.split("\n") if line.strip() and not line.strip().startswith(_COMMENT)
    ]


def is_decision(line: str) -> bool:
    """A line that decides or computes something in the domain and is not drawing or sound."""
    if _VIEW.search(line) is not None:
        return False
    return _DECISION.search(line) is not None or (
        _MONEY.search(line) is not None and _CALCULATION.search(line) is not None
    )


def is_domain_decision(line: str) -> bool:
    """P1: a domain decision, not an implementation detail. Stricter than
    `is_decision`: a comparison between local variables (gap > bestGap) or with 0 and 1 on an
    index does not count. It groups the decisions the model answers (R1b)."""
    if _VIEW.search(line) is not None:
        return False
    if _DOMAIN_DECISION.search(line) is not None:
        return True
    if _MONEY.search(line) is not None and _CALCULATION.search(line) is not None:
        return True
    return bool(
        _LOCAL_COMPARISON.search(line)
        and _MEMBER.search(line)
        and not _TRIVIAL_EQUALITY.search(line)
    )


def touches_money(text: str) -> bool:
    """A price, an amount, a tax or a payment in the text (F7: proposed criticality)."""
    return _MONEY.search(text) is not None


def is_visual(path: str, symbol: str | None = None) -> bool:
    """UI, drawing, animation or sound, by path, file name or function."""
    stem = path.rsplit("/", 1)[-1].split(".", 1)[0]
    name = (symbol or "").rsplit(".", 1)[-1]
    return bool(
        _VISUAL_DIR.search(path)
        or _VISUAL_NAME.search(stem)
        or (name and _VISUAL_NAME.search(name))
    )


def score_text(text: str) -> tuple[int, list[str]]:
    """Score by decision lines (F10). The other signals only describe the chunk."""
    reasons = [reason for reason, pattern, _ in _SIGNALS if pattern.search(text)]
    lines = _code_lines(text)
    decisions = sum(is_decision(line) for line in lines)
    if not decisions:
        reasons.append("sin decisiones")
        return 0, reasons
    reasons.append(f"{decisions} decisiones")
    score = min(decisions, _MAX_DECISIONS)
    if sum(_VIEW.search(line) is not None for line in lines) > decisions:
        reasons.append("mas dibujo que decisiones")
        score -= 3
    if len(lines) >= _DENSITY_MIN_LINES and 10 * decisions / len(lines) < _LOW_DENSITY:
        # Description only: a long part sends windows around its decisions, so
        # the filler never reaches the model and there is no need to penalize it.
        reasons.append("pocas decisiones para su tamano")
    if len(lines) <= _SMALL_FUNCTION_LINES:
        reasons.append("funcion pequena")
        score += 1
    if any(_REJECTION.search(line) for line in lines):
        reasons.append("rechazo explicito")
        score += 1
    elif _DATABASE_LOGIC.search(text):
        reasons.append("logica en la base de datos")
        score += 1
    # Never negative: with min_score 0 everything is still read.
    return max(score, 0), reasons


# Two decisions this close or closer (in lines) are the same rule: an if/else if, a
# condition and its consequence.
_GROUP_GAP: Final = 3


def decision_groups(text: str) -> list[tuple[int, int]]:
    """A chunk's decisions, grouped: (first, last) line, counting from 0 (R1b).
    A chunk without any is one whole group: the model can still say whether there is a rule."""
    lines = text.split("\n")
    groups: list[list[int]] = []
    for index, line in enumerate(lines):
        if not line.strip() or line.strip().startswith(_COMMENT) or not is_domain_decision(line):
            continue
        end = _call_end(lines, index)
        # Each declaration is its own rule, however close: five CHECKs in a row are five rules.
        declaration = _DECLARATION.match(line) is not None
        if groups and index - groups[-1][1] <= _GROUP_GAP and not declaration:
            groups[-1][1] = max(groups[-1][1], end)
        else:
            groups.append([index, end])
    return [(start, end) for start, end in groups] or [(0, len(lines) - 1)]


# A schema declaration starts its own decision: a Drizzle `check(`, a SQL CONSTRAINT, ALTER or
# CREATE statement.
_DECLARATION: Final = re.compile(r"(?i:\s*(?:check\s*\(|constraint\b|alter\s+table\b|create\b))")
# A call opened on a decision line (`check(` with its condition below) is cited whole, up to
# this many lines: otherwise the citation is just "check(" (Q1).
_MAX_CALL_LINES: Final = 6


def _call_end(lines: list[str], index: int) -> int:
    """The line where the parentheses opened on `lines[index]` close, or `index` itself."""
    depth = 0
    for offset, line in enumerate(lines[index : index + _MAX_CALL_LINES]):
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            return index + offset
    return index


def decision_windows(candidate: "Candidate") -> list["Candidate"]:
    """A long part, reduced to windows around its decisions. The lines are still
    the file's: each window is a contiguous chunk with its own range."""
    lines = candidate.text.split("\n")
    if len(lines) <= _WINDOW_FROM_LINES:
        return [candidate]
    spans: list[list[int]] = []
    for index, line in enumerate(lines):
        if not line.strip() or line.strip().startswith(_COMMENT) or not is_decision(line):
            continue
        start, end = max(0, index - _WINDOW), min(len(lines) - 1, index + _WINDOW)
        if spans and start <= spans[-1][1] + 2:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    windows = []
    for start, end in spans:
        piece = replace(candidate, start_line=candidate.start_line + start, part=0)
        piece.end_line = candidate.start_line + end
        piece.text = "\n".join(lines[start : end + 1])
        windows.append(piece)
    return windows or [candidate]


def _visual_penalty(candidate: "Candidate") -> None:
    if is_visual(candidate.path, candidate.symbol):
        # An explicit error or rejection weighs more than the path: subtract less.
        penalty = 1 if "error o rechazo" in candidate.reasons else _VISUAL_PENALTY
        candidate.score = max(candidate.score - penalty, 0)
        candidate.reasons.append("interfaz o dibujo")


def _split(candidate: Candidate) -> list[Candidate]:
    if len(candidate.text) <= MAX_CANDIDATE_CHARS:
        return [candidate]
    parts: list[Candidate] = []
    lines = candidate.text.split("\n")
    start = 0
    while start < len(lines):
        size, end = 0, start
        while end < len(lines) and (
            size + len(lines[end]) + 1 <= MAX_CANDIDATE_CHARS or end == start
        ):
            size += len(lines[end]) + 1
            end += 1
        parts.append(
            Candidate(
                candidate.revision_id, candidate.path, candidate.start_line + start,
                candidate.start_line + end - 1, "\n".join(lines[start:end]), candidate.score,
                candidate.reasons, candidate.symbol, candidate.logic_hash, len(parts),
            )
        )  # fmt: skip
        start = end
    return parts


def _outside_functions(
    revision: SourceRevision, lines: list[str], symbols: list[CodeSymbol]
) -> list[Candidate]:
    """The stretches of a file that no function or method covers (Q1). In business code many
    rules are declared there: a schema's checks, a role-to-permission table, the routes that
    require a permission, the configuration of sign-in."""
    covered = [False] * (len(lines) + 2)
    for symbol in symbols:
        for number in range(symbol.start_line, symbol.end_line + 1):
            if number < len(covered):
                covered[number] = True
    spans: list[Candidate] = []
    start: int | None = None
    for number in range(1, len(lines) + 2):
        inside = number <= len(lines) and not covered[number]
        if inside and start is None:
            start = number
        elif not inside and start is not None:
            text = "\n".join(lines[start - 1 : number - 1])
            if _code_lines(text):
                spans.append(
                    Candidate(revision.id, revision.metadata_.get("path", ""), start, number - 1,
                              text, 0, [])
                )  # fmt: skip
            start = None
    return spans


def select_candidates(
    session: Session,
    project_id: uuid.UUID,
    path_prefix: str | None,
    min_score: int = DEFAULT_MIN_SCORE,
) -> Selection:
    """The project's candidates, from highest to lowest score."""
    path = SourceRevision.metadata_["path"].as_string()
    revisions = session.execute(
        select(SourceRevision)
        .join(SourceItem, SourceItem.current_revision_id == SourceRevision.id)
        .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
        .where(
            SourceConnection.project_id == project_id,
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
            path.startswith(path_prefix, autoescape=True) if path_prefix else path.is_not(None),
        )
        .order_by(path)
    ).scalars()
    selection = Selection()
    code: list[SourceRevision] = []
    for revision in revisions:
        file_path = revision.metadata_.get("path", "")
        if not is_rule_source(file_path):
            selection.not_code += 1
        elif not is_test_path(file_path):
            # Tests check rules; they never go to the model nor change the order.
            code.append(revision)

    symbols: dict[uuid.UUID, list[CodeSymbol]] = defaultdict(list)
    for symbol in session.scalars(
        select(CodeSymbol).where(
            CodeSymbol.source_revision_id.in_([r.id for r in code]),
            CodeSymbol.kind.not_in(("class", NESTED)),
        )
    ):
        symbols[symbol.source_revision_id].append(symbol)

    found: list[Candidate] = []
    for revision in code:
        file_path = revision.metadata_.get("path", "")
        lines = (revision.text_content or "").split("\n")
        if language_for(file_path) is not None:
            for symbol in symbols[revision.id]:
                selection.functions_total += 1
                text = "\n".join(lines[symbol.start_line - 1 : symbol.end_line])
                whole = Candidate(
                    revision.id, file_path, symbol.start_line, symbol.end_line, text, 0, [],
                    symbol.qualified_name, symbol.logic_hash,
                )  # fmt: skip
                # A huge function is split before scoring: only the pieces with signals go.
                pieces: list[Candidate] = []
                for part in _split(whole):
                    part.score, part.reasons = score_text(part.text)
                    _visual_penalty(part)
                    pieces += decision_windows(part) if part.score >= min_score else [part]
                # Each piece with its number: it is part of its task's fingerprint in the queue.
                for number, piece in enumerate(pieces):
                    piece.part = number
                found += pieces
            # Code outside every function: schema constraints, permission tables, routes.
            for span in _outside_functions(revision, lines, symbols[revision.id]):
                selection.functions_total += 1
                span.score, span.reasons = score_text(span.text)
                _visual_penalty(span)
                found += decision_windows(span) if span.score >= min_score else [span]
            continue
        # SQL and languages without a grammar: their chunks.
        for chunk in session.scalars(
            select(ContentChunk)
            .where(ContentChunk.source_revision_id == revision.id)
            .order_by(ContentChunk.chunk_index)
        ):
            selection.functions_total += 1
            score, reasons = score_text(chunk.text_content)
            fragment = Candidate(
                revision.id, file_path, chunk.locator["start_line"],
                chunk.locator["end_line"], chunk.text_content, score, reasons,
            )  # fmt: skip
            _visual_penalty(fragment)
            found.append(fragment)
    for candidate in found:
        if candidate.score >= min_score:
            selection.candidates.extend(_split(candidate))
        else:
            selection.below_threshold += 1
    # Highest score first; on ties, by path and line: the order is always the same.
    selection.candidates.sort(key=lambda c: (-c.score, c.path, c.start_line))
    return selection
