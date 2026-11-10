"""Structural map of the code with tree-sitter: functions, methods and classes, with their lines.

No AI and nothing runs: tree-sitter only builds the syntax tree, even for code
with errors. Each symbol carries two fingerprints:

- `content_hash`, of the exact text;
- `logic_hash`, of its tokens without comments or whitespace: if only formatting or
  comments change, it does not change, and a rule relying on that symbol needs no review.
"""

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Final

import tree_sitter_c_sharp
import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_kotlin
import tree_sitter_php
import tree_sitter_python
import tree_sitter_ruby
import tree_sitter_rust
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

STRUCTURE_VERSION: Final = "1"
MAX_CALLS: Final = 200

_GRAMMARS: Final[dict[str, Callable[[], Any]]] = {
    "python": tree_sitter_python.language,
    "javascript": tree_sitter_javascript.language,
    "typescript": tree_sitter_typescript.language_typescript,
    "tsx": tree_sitter_typescript.language_tsx,
    "java": tree_sitter_java.language,
    "go": tree_sitter_go.language,
    "rust": tree_sitter_rust.language,
    "csharp": tree_sitter_c_sharp.language,
    "php": tree_sitter_php.language_php,
    "ruby": tree_sitter_ruby.language,
    "kotlin": tree_sitter_kotlin.language,
}
_EXTENSIONS: Final = {
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "tsx",
    ".java": "java", ".go": "go", ".rs": "rust", ".cs": "csharp", ".php": "php",
    ".rb": "ruby", ".kt": "kotlin", ".kts": "kotlin",
}  # fmt: skip

_FUNCTIONS: Final = frozenset(
    {
        "function_definition", "function_declaration", "generator_function_declaration",
        "method_definition", "method_declaration", "constructor_declaration", "function_item",
        "method", "singleton_method", "local_function_statement",
    }
)  # fmt: skip
_CLASSES: Final = frozenset(
    {
        "class_definition", "class_declaration", "interface_declaration", "enum_declaration",
        "record_declaration", "struct_declaration", "struct_item", "enum_item", "trait_item",
        "impl_item", "class", "module", "object_declaration", "trait_declaration", "type_spec",
    }
)  # fmt: skip
# A variable or field whose value is a function: `const total = (x) => …`.
_DECLARATORS: Final = frozenset(
    {"variable_declarator", "public_field_definition", "field_definition"}
)
_FUNCTION_VALUES: Final = frozenset({"arrow_function", "function_expression", "function"})
_CALLS: Final = frozenset(
    {
        "call", "call_expression", "method_invocation", "invocation_expression",
        "function_call_expression", "member_call_expression", "scoped_call_expression",
        "macro_invocation",
    }
)  # fmt: skip
_NAMES: Final = frozenset(
    {
        "identifier", "simple_identifier", "type_identifier", "property_identifier",
        "field_identifier", "name", "constant",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Symbol:
    kind: str  # "function", "method" or "class"
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    content_hash: str
    logic_hash: str
    calls: tuple[str, ...] = field(default=())


def language_for(path: str) -> str | None:
    dot = path.rfind(".")
    return _EXTENSIONS.get(path[dot:].lower()) if dot > path.rfind("/") else None


@cache
def _parser(language: str) -> Parser:
    return Parser(Language(_GRAMMARS[language]()))


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text else ""


def _name_of(node: Node) -> str:
    named = node.child_by_field_name("name") or node.child_by_field_name("type")
    if named is None:
        named = next((child for child in node.named_children if child.type in _NAMES), None)
    return _text(named)


def _callee(node: Node) -> str:
    target = (
        node.child_by_field_name("function")
        or node.child_by_field_name("name")
        or node.child_by_field_name("method")
        or node.child_by_field_name("macro")
        or (node.named_children[0] if node.named_children else None)
    )
    text = _text(target)
    for separator in ("->", "::", "?."):
        text = text.replace(separator, ".")
    return text.rsplit(".", 1)[-1].strip("()! ")


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _logic_hash(node: Node) -> str:
    """Fingerprint of the tokens without comments: formatting and comments do not change it."""
    tokens: list[str] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if "comment" in current.type:
            continue
        if current.child_count == 0:
            tokens.append(_text(current))
        else:
            stack.extend(reversed(current.children))
    return hashlib.sha256("\x1f".join(tokens).encode()).hexdigest()


def _calls(node: Node) -> tuple[str, ...]:
    found = {name for child in _walk(node) if child.type in _CALLS and (name := _callee(child))}
    return tuple(sorted(found)[:MAX_CALLS])


def _symbol(node: Node, kind: str, name: str, scope: list[str]) -> Symbol:
    return Symbol(
        kind=kind,
        name=name,
        qualified_name=".".join([*scope, name]),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        content_hash=hashlib.sha256(node.text or b"").hexdigest(),
        logic_hash=_logic_hash(node),
        calls=_calls(node),
    )


def parse_symbols(path: str, text: str) -> list[Symbol]:
    """Symbols of a code file; an empty list if the language is not supported.

    It does not enter function bodies: a nested function is part of the one that
    contains it. It does enter classes, to find their methods.
    """
    language = language_for(path)
    if language is None:
        return []
    tree = _parser(language).parse(text.encode("utf-8"))
    symbols: list[Symbol] = []
    stack: list[tuple[Node, list[str]]] = [(tree.root_node, [])]
    while stack:
        node, scope = stack.pop()
        if node.type in _FUNCTIONS:
            name = _name_of(node)
            if name:
                symbols.append(_symbol(node, "method" if scope else "function", name, scope))
            continue
        if node.type in _DECLARATORS:
            value = node.child_by_field_name("value")
            name = _name_of(node)
            if value is not None and value.type in _FUNCTION_VALUES and name:
                symbols.append(_symbol(node, "method" if scope else "function", name, scope))
                continue
        inner = scope
        if node.type in _CLASSES:
            name = _name_of(node)
            if name:
                symbols.append(_symbol(node, "class", name, scope))
                inner = [*scope, name]
        stack.extend((child, inner) for child in reversed(node.children))
    return sorted(symbols, key=lambda symbol: (symbol.start_line, symbol.end_line))
