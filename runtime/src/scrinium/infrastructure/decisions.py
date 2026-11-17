"""Decision nodes of the code, with two fingerprints each (ADR-011).

A business rule is implemented by decisions: a guard that refuses, a threshold, a change of
state, a formula, a table of values, a schema constraint, a validation. This module finds them
without a model, deterministically:

- Python, TypeScript and JavaScript with tree-sitter;
- SQL (PL/pgSQL included) with sqlglot's tokenizer and a small block scanner, since sqlglot does
  not parse function bodies.

Each node carries two fingerprints of its normalised form (BLAKE2b, 128 bits):

- `shape`, with literals replaced by their type: "the same decision";
- `value`, with literals and same-file constants resolved: "the same decision, same numbers".

Normalising drops comments and whitespace, renames local variables in order of appearance,
sorts the operands of commutative operators and turns `b > a` into `a < b`. So formatting,
renaming a local or swapping operands changes neither fingerprint; changing a threshold
changes `value` only.

A collection or table made only of values (`{'approved', 'posted'}`, a list of selection pairs,
a mapping of states to labels) has the shape of the kinds it holds, not of how many: adding or
removing a state, an entry or a row is a change of `value` ("the rule changed"), like a new
threshold. A set's values are sorted, and so are a table's entries by key: their order means
nothing.
"""

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from typing import Final

from sqlglot import TokenType
from sqlglot.tokens import Tokenizer
from tree_sitter import Node

from scrinium.infrastructure.code_structure import _parser, language_for, parse_symbols

# Raising it re-fingerprints every node. Anchors computed with another version are computed again
# from the code they cite (the cited revision, or the base commit in `scrinium check`), never
# compared as they are. Version 2: a collection of values has the same shape whatever its size.
DECISIONS_VERSION: Final = "2"


class Kind:
    GUARD = "guard"
    THRESHOLD = "threshold"
    STATE_CHANGE = "state_change"
    FORMULA = "formula"
    TABLE = "table"
    CONSTRAINT = "constraint"
    VALIDATION = "validation"


@dataclass(frozen=True)
class Decision:
    kind: str
    start_line: int
    end_line: int
    # The smallest function, method or class holding it; None at module level.
    symbol: str | None
    shape: str
    value: str
    # MinHash of the shape's 3-token shingles: finds the same decision after a real edit.
    sketch: str = ""


def _fingerprint(kind: str, canonical: str) -> str:
    return hashlib.blake2b(f"{kind}\x1f{canonical}".encode(), digest_size=16).hexdigest()


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text else ""


# --- Normalisation of a syntax subtree --------------------------------------------------------

_CONSTANT_NAME: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")
_NUMBERS: Final = frozenset({"integer", "float", "number"})
_STRINGS: Final = frozenset({"string", "template_string", "concatenated_string"})
_BOOLEANS: Final = frozenset({"true", "false"})
_NULLS: Final = frozenset({"none", "null", "undefined"})
_LITERALS: Final = _NUMBERS | _STRINGS | _BOOLEANS | _NULLS
_PUNCTUATION: Final = frozenset({"(", ")", ",", ":", ";", "{", "}", "[", "]"})
_COMMUTATIVE: Final = frozenset({"and", "or", "&&", "||", "==", "!=", "===", "!==", "*"})
_FLIPPED: Final = {">": "<", ">=": "<="}
_ATTRIBUTES: Final = frozenset({"attribute", "member_expression"})
_PROPERTY_NAMES: Final = frozenset({"property_identifier", "field_identifier"})
_CALLS: Final = frozenset({"call", "call_expression", "new_expression"})


def _literal(node: Node, keep_values: bool) -> str:
    kind = node.type
    if kind in _NUMBERS:
        if not keep_values:
            return "NUM"
        raw = _text(node).replace("_", "")
        try:
            number = float(raw)
        except ValueError:
            return raw
        return repr(int(number)) if number.is_integer() else repr(number)
    if kind in _STRINGS:
        if not keep_values:
            return "STR"
        return "'" + _text(node).strip("'\"`").replace("'", "\\'") + "'"
    if kind in _BOOLEANS:
        return _text(node).lower() if keep_values else "BOOL"
    return "NULL"


class _Canon:
    """Serialises one decision node. Local names are renamed per node, in order of appearance."""

    def __init__(self, keep_values: bool, constants: dict[str, str], probe: bool = False) -> None:
        self.keep_values = keep_values
        self.constants = constants
        self.locals: dict[str, str] = {}
        # A probe renders every local as "_": it orders commutative operands without depending
        # on which local appeared first.
        self.probe = probe

    def name(self, text: str) -> str:
        if _CONSTANT_NAME.match(text):
            if self.keep_values and text in self.constants:
                return self.constants[text]
            return text
        if self.probe:
            return "_"
        if text not in self.locals:
            self.locals[text] = f"v{len(self.locals)}"
        return self.locals[text]

    def __call__(self, node: Node) -> str:
        kind = node.type
        if "comment" in kind:
            return ""
        if kind in _LITERALS:
            return _literal(node, self.keep_values)
        if kind in {"identifier", "shorthand_property_identifier"}:
            return self.name(_text(node))
        if kind in _PROPERTY_NAMES:
            return _text(node)
        if kind == "parenthesized_expression":
            inner = [child for child in node.named_children if "comment" not in child.type]
            return self(inner[0]) if len(inner) == 1 else self._generic(node)
        if kind in _ATTRIBUTES:
            target = node.child_by_field_name("object")
            field = node.child_by_field_name("attribute") or node.child_by_field_name("property")
            return f"{self(target) if target is not None else ''}.{_text(field)}"
        if kind in _CALLS:
            # The callee keeps its name: `float_compare(...)` means something, unlike a local.
            callee = node.child_by_field_name("function") or node.child_by_field_name("constructor")
            arguments = node.child_by_field_name("arguments")
            head = (
                self(callee)
                if callee is not None and callee.type in _ATTRIBUTES
                else (_text(callee))
            )
            return f"(call {head} {self(arguments) if arguments is not None else ''})"
        if kind == "keyword_argument":
            value = node.child_by_field_name("value")
            rendered = self(value) if value is not None else ""
            return f"{_text(node.child_by_field_name('name'))}={rendered}"
        return self._values(node) or self._operator(node) or self._generic(node)

    def _pure(self, node: Node) -> bool:
        """A literal, a constant, or a collection or table of those: values, no logic."""
        if node.type in _LITERALS:
            return True
        if node.type == "identifier":
            return bool(_CONSTANT_NAME.match(_text(node)))
        if node.type in _COLLECTIONS:
            return all(self._pure(item) for item in _items(node))
        if node.type in _MAPPINGS:
            for pair in _items(node):
                key, value = pair.child_by_field_name("key"), pair.child_by_field_name("value")
                if pair.type != "pair" or key is None or value is None:
                    return False
                if not (key.type in _PROPERTY_NAMES or self._pure(key)) or not self._pure(value):
                    return False
            return True
        return False

    def _values(self, node: Node) -> str | None:
        """A collection or table of values: its shape is the kinds it holds (version 2)."""
        if node.type not in _COLLECTIONS | _MAPPINGS or not self._pure(node):
            return None
        if node.type in _COLLECTIONS:
            items = [self(item) for item in _items(node)]
            if not self.keep_values:
                return f"(values {' '.join(sorted(set(items)))})"
            ordered = sorted(items) if node.type == "set" else items
            return f"(values {node.type} {' '.join(ordered)})"
        pairs = []
        for pair in _items(node):
            key, value = pair.child_by_field_name("key"), pair.child_by_field_name("value")
            if key is None or value is None:
                continue  # Not possible after _pure; it keeps the types narrow.
            name = _text(key) if key.type in _PROPERTY_NAMES and self.keep_values else (
                "STR" if key.type in _PROPERTY_NAMES else self(key)
            )  # fmt: skip
            pairs.append(f"{name}:{self(value)}")
        if not self.keep_values:
            return f"(table {' '.join(sorted(set(pairs)))})"
        return f"(table {' '.join(sorted(pairs))})"

    def _operator(self, node: Node) -> str | None:
        """Binary comparisons and logic: flip `>`, sort commutative operands."""
        if node.type not in {"comparison_operator", "binary_expression", "boolean_operator"}:
            return None
        parts = [child for child in node.children if "comment" not in child.type]
        if len(parts) != 3:
            return None
        left, operator, right = parts
        symbol = _text(operator)
        if symbol in _FLIPPED:
            left, right, symbol = right, left, _FLIPPED[symbol]
        ordered = [left, right]
        if symbol in _COMMUTATIVE:
            probe = _Canon(self.keep_values, self.constants, probe=True)
            ordered.sort(key=probe)
        return f"({symbol} {self(ordered[0])} {self(ordered[1])})"

    def _generic(self, node: Node) -> str:
        if node.child_count == 0:
            text = _text(node)
            return "" if text in _PUNCTUATION else text
        parts = [part for child in node.children if (part := self(child))]
        return f"({node.type} {' '.join(parts)})"


SKETCH_SIZE: Final = 32
_SHINGLE: Final = 3
_PRIME: Final = (1 << 61) - 1
# Fixed coefficients of the MinHash permutations: the same sketch on every machine and run.
_PERMUTATIONS: Final = tuple(
    (
        int.from_bytes(hashlib.blake2b(f"a{i}".encode(), digest_size=8).digest()) % _PRIME | 1,
        int.from_bytes(hashlib.blake2b(f"b{i}".encode(), digest_size=8).digest()) % _PRIME,
    )
    for i in range(SKETCH_SIZE)
)
_TOKEN: Final = re.compile(r"[()]|[^\s()]+")


def sketch_of(canonical: str) -> str:
    """MinHash of the 3-token shingles of a canonical form, as hex (8 digits per value)."""
    tokens = _TOKEN.findall(canonical)
    shingles = {
        " ".join(tokens[i : i + _SHINGLE]) for i in range(max(1, len(tokens) - _SHINGLE + 1))
    }
    hashed = [
        int.from_bytes(hashlib.blake2b(shingle.encode(), digest_size=8).digest())
        for shingle in shingles
    ]
    values = (min((a * h + b) % _PRIME for h in hashed) & 0xFFFFFFFF for a, b in _PERMUTATIONS)
    return "".join(f"{value:08x}" for value in values)


def similarity(first: str, second: str) -> float:
    """Estimated Jaccard similarity of two sketches; 0 if either is missing."""
    if not first or len(first) != len(second):
        return 0.0
    pairs = zip(range(0, len(first), 8), range(0, len(second), 8), strict=True)
    same = sum(first[i : i + 8] == second[j : j + 8] for i, j in pairs)
    return same / (len(first) // 8)


def _fingerprints(kind: str, nodes: list[Node], constants: dict[str, str]) -> tuple[str, str, str]:
    canon = _Canon(keep_values=False, constants=constants)
    parts = [canon(node) for node in nodes]
    shape = " ".join(parts)
    value = " ".join(_Canon(keep_values=True, constants=constants)(node) for node in nodes)
    # A guard's sketch is its condition: the exit (`raise UserError(...)`) is alike everywhere
    # and would make different guards look the same.
    sketched = parts[0] if kind == Kind.GUARD else shape
    return _fingerprint(kind, shape), _fingerprint(kind, value), sketch_of(sketched)


# --- Python, TypeScript and JavaScript --------------------------------------------------------

_IFS: Final = frozenset({"if_statement", "elif_clause"})
_EXITS: Final = frozenset({"return_statement", "raise_statement", "throw_statement"})
_COMPARISON_OPERATORS: Final = frozenset(
    {"<", "<=", ">", ">=", "==", "!=", "===", "!==", "in", "not in", "is not", "<>"}
)
_ARITHMETIC: Final = frozenset({"*", "/", "%", "-", "+", "//", "**"})
_TRIVIAL_NUMBERS: Final = frozenset({"0", "1", "-1", "0.0", "1.0"})
_ASSIGNMENTS: Final = frozenset({"assignment", "assignment_expression"})
_COLLECTIONS: Final = frozenset({"tuple", "list", "set", "array"})
_MAPPINGS: Final = frozenset({"dictionary", "object"})
_WRITES: Final = frozenset({"write", "update", "create", "set", "setState"})
_VALIDATORS: Final = frozenset({"z", "Joi", "yup", "v"})
_VALIDATION_METHODS: Final = frozenset(
    {
        "min", "max", "length", "regex", "email", "positive", "nonnegative", "negative", "int",
        "gt", "gte", "lt", "lte", "url", "uuid", "refine", "multipleOf", "nonempty", "enum",
    }
)  # fmt: skip
_FIELD_LIMITS: Final = frozenset(
    {"gt", "ge", "lt", "le", "min_length", "max_length", "pattern", "multiple_of"}
)
_DOMAIN_OPERATORS: Final = frozenset(
    {"=", "!=", "<", ">", "<=", ">=", "in", "not in", "like", "ilike", "=like", "child_of"}
)
# Odoo `models.Constraint`, drizzle `check(...)` / `unique(...)` / `foreignKey(...)`.
_CONSTRAINT_CALLS: Final = frozenset(
    {"Constraint", "UniqueIndex", "check", "unique", "uniqueIndex", "foreignKey"}
)
_ORDERING: Final = frozenset({"<", "<=", ">", ">="})
_CLAMPS: Final = frozenset({"min", "max", "clamp", "round", "floor", "ceil", "float_round"})
# `requirePermission('users:manage')`, `has_group('base.group_user')`, `can('edit')`.
_PERMISSION_CALL: Final = re.compile(
    r"^(?:require|ensure|assert|authorize|check|has|can|user_has|is_allowed)(?:[A-Z_]\w*)?$"
)
_LOOPS: Final = frozenset({"for_statement", "while_statement", "for_in_statement"})
_STATE_KEYS: Final = re.compile(r"^['\"`]?(?:state|status|stage|phase)['\"`]?$", re.I)
_BRANCHES: Final = frozenset(
    {
        "if_statement",
        "elif_clause",
        "else_clause",
        "switch_case",
        "case_clause",
        "conditional_expression",
    }
)
_FUNCTION_BOUNDARIES: Final = frozenset(
    {"function_definition", "function_declaration", "method_definition", "arrow_function"}
)
_WRAPPERS: Final = frozenset(
    {"parenthesized_expression", "conditional_expression", "ternary_expression"}
)
_MAX_GUARD_LINES: Final = 40


def _items(node: Node) -> list[Node]:
    return [child for child in node.named_children if "comment" not in child.type]


def _children(node: Node) -> list[Node]:
    return [child for child in node.named_children if "comment" not in child.type]


def _is_literalish(node: Node) -> bool:
    """A literal, a constant name, a collection of literals, or `Config.MAX_X`."""
    if node.type in _LITERALS - _NULLS:
        return True
    if node.type == "unary_operator" or node.type == "unary_expression":
        inner = _children(node)
        return bool(inner) and _is_literalish(inner[-1])
    if node.type in {"identifier"}:
        return bool(_CONSTANT_NAME.match(_text(node)))
    if node.type in _ATTRIBUTES:
        field = node.child_by_field_name("attribute") or node.child_by_field_name("property")
        return bool(_CONSTANT_NAME.match(_text(field)))
    if node.type in _COLLECTIONS:
        items = _children(node)
        return bool(items) and all(_is_literalish(item) for item in items)
    return False


def _is_threshold(node: Node) -> bool:
    if node.type not in {"comparison_operator", "binary_expression"}:
        return False
    parts = [child for child in node.children if "comment" not in child.type]
    if len(parts) != 3 or _text(parts[1]) not in _COMPARISON_OPERATORS:
        return False
    left, operator, right = parts
    if any(side.type in _NULLS for side in (left, right)):
        return False
    if _is_literalish(left) or _is_literalish(right):
        return True
    # Two quantities compared by order (`cost <= candies`), outside a loop header.
    if _text(operator) in _ORDERING:
        return node.parent is None or node.parent.type not in _LOOPS
    # Two fields compared for equality (`held.size === mission.items.length`).
    return left.type in _ATTRIBUTES and right.type in _ATTRIBUTES


def _is_arithmetic(node: Node) -> bool:
    return node.type in {"binary_operator", "binary_expression"} and (
        _text(node.child_by_field_name("operator")) in _ARITHMETIC
    )


def _has_meaningful_operand(node: Node) -> bool:
    """Some arithmetic step inside `node` uses a literal (not 0 or 1) or a named constant."""
    for inner in _walk(node):
        if _is_arithmetic(inner):
            for side in (inner.child_by_field_name("left"), inner.child_by_field_name("right")):
                if (
                    side is not None
                    and _is_literalish(side)
                    and _text(side) not in _TRIVIAL_NUMBERS
                ):
                    return True
    return False


def _is_plain_clamp(node: Node) -> bool:
    """`min(a, b)` or `Math.max(...)`, not a method of a builder like `z.string().min(3)`."""
    callee = _text(node.child_by_field_name("function"))
    return "." not in callee or callee.split(".", 1)[0] in {"Math", "math", "np", "numpy"}


def _is_formula(node: Node) -> bool:
    """The outermost arithmetic of an expression that uses a constant, or a clamp with one."""
    if node.type in _CALLS and _callee_name(node) in _CLAMPS and _is_plain_clamp(node):
        arguments = _children(node.child_by_field_name("arguments") or node)
        return (
            any(_is_literalish(a) and _text(a) not in _TRIVIAL_NUMBERS for a in arguments)
            or (len(arguments) >= 2 and any(a.type in _ATTRIBUTES for a in arguments))
            or _has_meaningful_operand(node)
        )
    if not _is_arithmetic(node):
        return False
    parent = node.parent
    while parent is not None and parent.type in _WRAPPERS:
        parent = parent.parent
    if parent is not None and _is_arithmetic(parent):
        return False
    if _has_meaningful_operand(node):
        return True
    fields = [
        side
        for side in (node.child_by_field_name("left"), node.child_by_field_name("right"))
        if side is not None and side.type in _ATTRIBUTES
    ]
    # Whole units of a field (`points // required * required`), anywhere in the expression.
    for inner in _walk(node):
        if _is_arithmetic(inner) and _text(inner.child_by_field_name("operator")) in {"//", "%"}:
            sides = (inner.child_by_field_name("left"), inner.child_by_field_name("right"))
            if any(side is not None and side.type in _ATTRIBUTES for side in sides):
                return True
    # A computed field: `order.amount = order.percent * order.total`, or returned.
    if parent is None or not fields:
        return False
    if parent.type in _ASSIGNMENTS:
        target = parent.child_by_field_name("left")
        return target is not None and target.type in _ATTRIBUTES
    return parent.type == "return_statement"


def _is_permission_check(node: Node) -> bool:
    if node.type not in _CALLS or not _PERMISSION_CALL.match(_callee_name(node)):
        return False
    arguments = node.child_by_field_name("arguments")
    return any(
        argument.type in _STRINGS for argument in (_children(arguments) if arguments else [])
    )


def _guard_exit(consequence: Node | None) -> Node | None:
    if consequence is None:
        return None
    if consequence.type in _EXITS:
        return consequence
    for statement in _children(consequence):
        if statement.type in _EXITS:
            return statement
        if statement.type == "expression_statement":
            inner = _children(statement)
            # `res.status(403).json(...)` / `redirect(...)` are exits in web handlers.
            if (
                inner
                and inner[0].type in _CALLS
                and re.search(r"\b(?:status|redirect|abort|reject)\(", _text(inner[0]))
            ):
                return statement
    return None


def _assigned_name(node: Node) -> str:
    target = node.child_by_field_name("left") or node.child_by_field_name("name")
    return _text(target)


def _callee_name(node: Node) -> str:
    callee = node.child_by_field_name("function") or node.child_by_field_name("constructor")
    text = _text(callee)
    return text.rsplit(".", 1)[-1]


def _in_branch(node: Node) -> bool:
    """Inside an if, else, case or conditional expression of the same function."""
    parent = node.parent
    while parent is not None and parent.type not in _FUNCTION_BOUNDARIES:
        if parent.type in _BRANCHES:
            return True
        parent = parent.parent
    return False


def _is_state_write(node: Node) -> bool:
    """`self.state = 'sale'`, `order.status = DONE`, `vals['state'] = 'x'`, `x.write({...})`,
    `{"state": "sale"}`, and a field set to a literal inside a branch."""
    if node.type in _ASSIGNMENTS:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            return False
        targets_field = left.type in _ATTRIBUTES or left.type in {
            "subscript",
            "subscript_expression",
        }
        if not targets_field or not _is_literalish(right):
            return False
        return right.type not in _NUMBERS or _in_branch(node)
    if node.type == "pair":
        key, value = node.child_by_field_name("key"), node.child_by_field_name("value")
        return (
            key is not None
            and value is not None
            and bool(_STATE_KEYS.match(_text(key)))
            and value.type in _STRINGS
            and not (
                node.parent is not None
                and node.parent.parent is not None
                and node.parent.parent.parent is not None
                and node.parent.parent.parent.type in _CALLS
                and _callee_name(node.parent.parent.parent) in _WRITES
            )
        )
    if node.type in _CALLS and _callee_name(node) in _WRITES:
        arguments = node.child_by_field_name("arguments")
        mapping = (
            next((a for a in _children(arguments) if a.type in _MAPPINGS), None)
            if arguments
            else None
        )
        if mapping is None:
            return False
        return any(
            (value := pair.child_by_field_name("value")) is not None and value.type in _STRINGS
            for pair in _children(mapping)
            if pair.type == "pair"
        )
    return False


def _is_table(node: Node) -> bool:
    """A mapping of literals (permissions, prices) or a list of literal pairs (selections)."""
    if node.type in _MAPPINGS:
        pairs = [pair for pair in _children(node) if pair.type == "pair"]
        return len(pairs) >= 3 and all(
            (value := pair.child_by_field_name("value")) is not None
            and (_is_literalish(value) or value.type in _MAPPINGS)
            for pair in pairs
        )
    if node.type in {"list", "array"}:
        items = _children(node)
        return len(items) >= 2 and all(
            item.type in {"tuple", "array"}
            and len(_children(item)) == 2
            and all(part.type in _STRINGS for part in _children(item))
            for item in items
        )
    return False


def _is_domain_condition(node: Node) -> bool:
    """An Odoo-style domain term: `('state', '=', 'sale')`."""
    if node.type not in {"tuple", "array"}:
        return False
    items = _children(node)
    return (
        len(items) == 3
        and items[0].type in _STRINGS
        and items[1].type in _STRINGS
        and _text(items[1]).strip("'\"") in _DOMAIN_OPERATORS
    )


def _is_validation(node: Node) -> bool:
    if node.type not in _CALLS or (node.parent is not None and node.parent.type in _ATTRIBUTES):
        return False
    text = _text(node)
    callee = _callee_name(node)
    if callee in {"Field", "constr", "conint", "confloat"}:
        arguments = node.child_by_field_name("arguments")
        return any(
            child.type == "keyword_argument"
            and _text(child.child_by_field_name("name")) in _FIELD_LIMITS
            for child in (_children(arguments) if arguments else [])
        )
    root = re.match(r"([A-Za-z_]\w*)\.", text)
    if root is None or root.group(1) not in _VALIDATORS:
        return False
    return any(f".{method}(" in text for method in _VALIDATION_METHODS)


def _is_constraint(node: Node) -> bool:
    if node.type in _CALLS:
        return _callee_name(node) in _CONSTRAINT_CALLS
    if node.type in _ASSIGNMENTS:
        return _assigned_name(node) == "_sql_constraints"
    return False


def _constants(root: Node) -> dict[str, str]:
    """Constants of the file with a literal value, one alias deep: `MAX = 3`, `const B = MAX`."""
    found: dict[str, Node] = {}
    for node in _walk(root):
        if node.type == "assignment":
            name, value = node.child_by_field_name("left"), node.child_by_field_name("right")
        elif node.type == "variable_declarator":
            name, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        else:
            continue
        if name is None or value is None or name.type != "identifier":
            continue
        key = _text(name)
        if _CONSTANT_NAME.match(key) and key not in found:
            found[key] = value
    resolved: dict[str, str] = {}
    for key, value in found.items():
        if value.type == "identifier" and _text(value) in found:
            value = found[_text(value)]
        if value.type in _LITERALS:
            resolved[key] = _literal(value, keep_values=True)
    return resolved


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _code_nodes(root: Node) -> Iterator[tuple[str, list[Node], int, int]]:
    """(kind, nodes to fingerprint, first line, last line) of each decision."""
    for node in _walk(root):
        if node.type in _IFS:
            condition = node.child_by_field_name("condition")
            exit_node = _guard_exit(node.child_by_field_name("consequence"))
            if condition is not None and exit_node is not None:
                end = min(exit_node.end_point[0], node.start_point[0] + _MAX_GUARD_LINES)
                action = _children(exit_node)[:1]
                yield Kind.GUARD, [condition, *action], node.start_point[0], end
            continue
        if _is_threshold(node) or _is_domain_condition(node):
            yield Kind.THRESHOLD, [node], node.start_point[0], node.end_point[0]
        elif _is_formula(node):
            yield Kind.FORMULA, [node], node.start_point[0], node.end_point[0]
        elif _is_state_write(node):
            yield Kind.STATE_CHANGE, [node], node.start_point[0], node.end_point[0]
        elif _is_constraint(node):
            yield Kind.CONSTRAINT, [node], node.start_point[0], node.end_point[0]
        elif _is_permission_check(node):
            yield Kind.GUARD, [node], node.start_point[0], node.end_point[0]
        elif _is_validation(node):
            yield Kind.VALIDATION, [node], node.start_point[0], node.end_point[0]
        elif _is_table(node):
            yield Kind.TABLE, [node], node.start_point[0], node.end_point[0]


def _innermost(symbols: list[tuple[str, int, int]], start: int, end: int) -> str | None:
    holding = [s for s in symbols if s[1] <= start and s[2] >= end]
    return min(holding, key=lambda s: s[2] - s[1])[0] if holding else None


def _parse_code(path: str, text: str, language: str) -> list[Decision]:
    root = _parser(language).parse(text.encode("utf-8")).root_node
    constants = _constants(root)
    symbols = [(s.qualified_name, s.start_line, s.end_line) for s in parse_symbols(path, text)]
    found: list[Decision] = []
    for kind, nodes, start, end in _code_nodes(root):
        shape, value, sketch = _fingerprints(kind, nodes, constants)
        first, last = start + 1, end + 1
        symbol = _innermost(symbols, first, last)
        found.append(Decision(kind, first, last, symbol, shape, value, sketch))
    return found


# --- SQL and PL/pgSQL -------------------------------------------------------------------------

_SQL_LITERALS: Final = {TokenType.STRING: "STR", TokenType.NUMBER: "NUM"}
_SQL_BLOCKS: Final = (
    # (kind, pattern of the first line, pattern of the last line)
    (Kind.GUARD, re.compile(r"^\s*(?:ELS)?IF\b", re.I), re.compile(r"\bRAISE\b", re.I)),
    (Kind.THRESHOLD, re.compile(r"\bCASE\b", re.I), re.compile(r"\bEND\b(?!\s+IF)", re.I)),
    (Kind.CONSTRAINT, re.compile(r"\b(?:CHECK\s*\(|CONSTRAINT\b|REFERENCES\b|UNIQUE\b)", re.I),
     re.compile(r"[,;)]\s*$")),
    (Kind.CONSTRAINT, re.compile(r"^\s*CREATE\s+POLICY\b", re.I), re.compile(r";\s*$")),
    (Kind.STATE_CHANGE, re.compile(r"^\s*UPDATE\b", re.I), re.compile(r";\s*$")),
    (Kind.STATE_CHANGE, re.compile(r"^\s*NEW\.\w+\s*:=", re.I), re.compile(r";\s*$")),
)  # fmt: skip
_SQL_MAX_LINES: Final = 30
_SQL_LOCAL: Final = re.compile(r"^(?:v|p|l)_\w+$", re.I)


@cache
def _sql_tokenizer() -> Tokenizer:
    return Tokenizer()


def _sql_canonical(fragment: str, keep_values: bool) -> str:
    try:
        tokens = _sql_tokenizer().tokenize(fragment)
    except Exception:  # the tokenizer raises plain errors on odd input
        return " ".join(fragment.split()).lower()
    names: dict[str, str] = {}
    out: list[str] = []
    for token in tokens:
        if token.token_type in _SQL_LITERALS:
            out.append(token.text if keep_values else _SQL_LITERALS[token.token_type])
            continue
        text = token.text.lower()
        if token.token_type is TokenType.VAR and _SQL_LOCAL.match(text):
            text = names.setdefault(text, f"v{len(names)}")
        out.append(text)
    return " ".join(out)


def _parse_sql(text: str) -> list[Decision]:
    lines = text.splitlines()
    found: list[Decision] = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith("--"):
            continue
        for kind, opens, closes in _SQL_BLOCKS:
            if not opens.search(line):
                continue
            end = index
            while (
                end < len(lines) - 1
                and end - index < _SQL_MAX_LINES
                and not closes.search(lines[end])
            ):
                end += 1
            if not closes.search(lines[end]):
                continue
            fragment = "\n".join(lines[index : end + 1])
            shape = _sql_canonical(fragment, keep_values=False)
            found.append(
                Decision(
                    kind,
                    index + 1,
                    end + 1,
                    None,
                    _fingerprint(kind, shape),
                    _fingerprint(kind, _sql_canonical(fragment, keep_values=True)),
                    sketch_of(shape),
                )
            )
            break
    return found


# --- Entry points -----------------------------------------------------------------------------


_CODE_LANGUAGES: Final = frozenset({"python", "typescript", "tsx", "javascript"})


def supports(path: str) -> bool:
    """Whether decisions are found in files like this one."""
    return path.lower().endswith(".sql") or language_for(path) in _CODE_LANGUAGES


def parse_decisions(path: str, text: str) -> list[Decision]:
    """The decisions of a code file, by first line; [] for languages without support."""
    if path.lower().endswith(".sql"):
        return _parse_sql(text)
    language = language_for(path)
    if language not in _CODE_LANGUAGES:
        return []
    return sorted(_parse_code(path, text, language), key=lambda d: (d.start_line, d.end_line))


def anchor_for(decisions: list[Decision], start: int, end: int) -> Decision | None:
    """The decision that best anchors a citation of lines `start`-`end`.

    Prefer a guard, then the node that overlaps the citation most; ties go to the smaller
    node, which is the more specific one.
    """
    overlapping = [d for d in decisions if d.start_line <= end and d.end_line >= start]
    if not overlapping:
        return None

    def rank(decision: Decision) -> tuple[int, int, int]:
        overlap = min(end, decision.end_line) - max(start, decision.start_line) + 1
        return (
            decision.kind == Kind.GUARD,
            overlap,
            -(decision.end_line - decision.start_line),
        )

    return max(overlapping, key=rank)
