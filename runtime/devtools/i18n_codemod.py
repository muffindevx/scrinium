"""Wraps the console's visible Spanish text in `t()` (phase 5.5). Run once; safe to run again.

- JSX text with words → `{t("…")}` (whitespace around it kept, so JSX renders the same).
- Display attributes (title, placeholder, aria-label, label, description, alt) → `={t("…")}`.
- Prose string literals and template literals in code (labels, messages) → `t("…")`, with
  `${x}` becoming `{0}` and an argument: `t("{0} reglas", x)`.

Never touches imports, types, comparisons, object keys, class names, paths or calls that take
an identifier (navigate, fetch, storage). Writes `ui/src/lib/i18n-keys.json` with every key.

Usage: uv run --project runtime python runtime/devtools/i18n_codemod.py ui/src
"""

import json
import re
import sys
from pathlib import Path
from typing import Final

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

_TSX = Language(tree_sitter_typescript.language_tsx())
_TS = Language(tree_sitter_typescript.language_typescript())
_ATTRIBUTES = {
    "title",
    "placeholder",
    "aria-label",
    "label",
    "description",
    "alt",
    "aria-description",
}
_SKIP_CALLS = {
    "navigate", "getJson", "mutate", "request", "querySelector", "querySelectorAll", "getItem",
    "setItem", "removeItem", "matchMedia", "cn", "t", "fetch", "addEventListener", "Error",
    "useParams", "startsWith", "endsWith", "includes", "split", "replace", "join", "test",
    "toLocaleString", "Intl.NumberFormat", "Intl.DateTimeFormat", "toLocaleDateString", "assign",
}  # fmt: skip
_SKIP_PARENTS = {
    "import_statement", "export_statement_source", "literal_type",
    "switch_case", "property_identifier", "index_signature", "type_annotation", "enum_body",
}  # fmt: skip
# Data that looks like prose: HTTP, CSS, SQL and the server's own markers. Never wrapped.
_DATA: Final = frozenset(
    {
        "POST",
        "Content-Disposition",
        "X-Backup-Passphrase",
        "SELECT *",
        "EMAIL_1",
        "max-h-[32rem] overflow-y-auto",
        "Pendiente de redactar.",
    }
)
_WORDS = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñü]{2,}")
_SPANISH = re.compile(
    r"[áéíóúñ¿¡]|\b(de|la|el|los|las|una|un|para|con|sin|que|en|del|por|se|no|y)\b"
)
_NOT_PROSE = re.compile(r"^(/|https?:|\.|#|[a-z_\-]+$|[a-z]+(:[a-z\-\[\]]+)+)|\b(?:px|rem)\b")


# Text that is code, a command or a path stays as written.
_CODE: Final = re.compile(r"/|_|--|\bscrinium\b|^\.?[a-z]+\.[a-z]+$|^tokens$")


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode()


def _prose(text: str) -> bool:
    text = text.strip()
    if text in _DATA:
        return False
    if not _WORDS.search(text) or _NOT_PROSE.search(text):
        return False
    return bool(_SPANISH.search(text)) or (text[:1].isupper() and " " in text) or text[:1].isupper()


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _callee(call: Node, source: bytes) -> str:
    function = call.child_by_field_name("function")
    name = _text(function, source) if function is not None else ""
    return name.rsplit(".", 1)[-1] if not name.startswith("Intl.") else name


def _skipped(node: Node, source: bytes) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in _SKIP_PARENTS:
            return True
        if parent.type == "binary_expression":
            operator = parent.child_by_field_name("operator")
            if operator is None or _text(operator, source) not in {"&&", "||", "??"}:
                return True
        if parent.type == "pair" and parent.child_by_field_name("key") == node:
            return True
        if parent.type == "jsx_attribute":
            name = _text(parent.children[0], source)
            return name not in _ATTRIBUTES
        if parent.type in {"call_expression", "new_expression"}:
            function = parent.child_by_field_name("function") or parent.child_by_field_name(
                "constructor"
            )
            if function is not None and _callee(parent, source) in _SKIP_CALLS:
                return True
            if function is not None and function.type == "identifier":
                name = _text(function, source)
                if name in _SKIP_CALLS:
                    return True
            return False
        if parent.type in {"statement_block", "program", "jsx_expression", "arrow_function"}:
            return False
        parent = parent.parent
    return False


def _template(node: Node, source: bytes) -> tuple[str, list[str]] | None:
    """`a ${x} b` → ("a {0} b", ["x"]); None if it has no prose."""
    key, last = "", node.start_byte + 1
    args: list[str] = []
    for child in node.named_children:
        if child.type == "template_substitution":
            key += source[last : child.start_byte].decode()
            expression = child.named_children[0]
            key += "{" + str(len(args)) + "}"
            args.append(_text(expression, source))
            last = child.end_byte
    key += source[last : node.end_byte - 1].decode()
    static = re.sub(r"\{\d+\}", " ", key)
    if "\n" in key or not _prose(static) or (not _SPANISH.search(static) and " " not in static):
        return None
    return key, args


def _edits(root: Node, source: bytes) -> list[tuple[int, int, str, str]]:
    edits: list[tuple[int, int, str, str]] = []

    def visit(node: Node) -> None:
        kind = node.type
        if kind == "jsx_text":
            raw = _text(node, source)
            core = " ".join(raw.split())
            if core and re.search(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{3,}", core) and not _CODE.search(core):
                lead = raw[: len(raw) - len(raw.lstrip())]
                trail = raw[len(raw.rstrip()) :]
                edits.append(
                    (node.start_byte, node.end_byte, f"{lead}{{t({_quote(core)})}}{trail}", core)
                )
            return
        if kind == "string" and node.parent is not None:
            value = _text(node, source)[1:-1]
            in_plural = (
                node.parent.type == "arguments"
                and node.parent.parent is not None
                and _callee(node.parent.parent, source) == "plural"
            )
            if in_plural and _WORDS.search(value) and "\\" not in value:
                edits.append((node.start_byte, node.end_byte, f"t({_quote(value)})", value))
                return
            if "\\" in value or not _prose(value):
                return
            if node.parent.type == "jsx_attribute":
                if _text(node.parent.children[0], source) in _ATTRIBUTES:
                    edits.append((node.start_byte, node.end_byte, f"{{t({_quote(value)})}}", value))
                return
            if not _skipped(node, source):
                edits.append((node.start_byte, node.end_byte, f"t({_quote(value)})", value))
            return
        if kind == "template_string" and not _skipped(node, source):
            found = _template(node, source)
            if found is not None:
                key, args = found
                call = f"t({_quote(key)}{''.join(', ' + a for a in args)})"
                edits.append((node.start_byte, node.end_byte, call, key))
                return
        for child in node.children:
            visit(child)

    visit(root)
    return edits


def convert(path: Path, keys: set[str]) -> bool:
    source = path.read_bytes()
    parser = Parser(_TSX if path.suffix == ".tsx" else _TS)
    tree = parser.parse(source)
    edits = _edits(tree.root_node, source)
    if not edits:
        return False
    out = source
    for start, end, text, key in sorted(edits, reverse=True):
        out = out[:start] + text.encode() + out[end:]
        keys.add(key)
    text = out.decode()
    if 'from "@/lib/i18n"' not in text:
        text = 'import { t } from "@/lib/i18n"\n' + text
    path.write_text(text, encoding="utf-8")
    return True


def main(root: str) -> int:
    base = Path(root)
    keys: set[str] = set()
    changed = 0
    for path in sorted([*base.rglob("*.tsx"), *base.rglob("*.ts")]):
        if path.name.endswith((".test.ts", ".test.tsx", ".d.ts")) or path.name.startswith("i18n"):
            continue
        if "components/ui" in path.as_posix():
            continue
        changed += convert(path, keys)
    catalog = base / "lib" / "i18n-keys.json"
    existing = set(json.loads(catalog.read_text())) if catalog.exists() else set()
    catalog.write_text(json.dumps(sorted(keys | existing), ensure_ascii=False, indent=1) + "\n")
    print(f"{changed} archivos, {len(keys | existing)} claves")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
