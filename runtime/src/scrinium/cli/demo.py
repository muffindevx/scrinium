"""`scrinium demo`: the 5-minute demo (phase 5.3). No model, no GPU, no network.

1. Creates the example shop as a Git repository, with three verified rules already in
   `.scrinium/rules/` (what a team commits after reviewing them in the console).
2. Lists those rules.
3. On a branch, makes the change a campaign would: free shipping from 60 EUR instead of 40.
4. Runs `scrinium check` against `main`: the rule changed and its file did not, so it fails and
   says which rule, where, and what to do.

Everything happens in the folder it is given (by default `./scrinium-demo`); it never touches
Scrinium's data directory. To see the same repository in the console, add the folder as a
source after `scrinium start`.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scrinium.application.check.engine import check
from scrinium.application.check.git import Reader, Repository
from scrinium.application.check.report import to_markdown
from scrinium.application.check.rule_files import RULES_DIR, FileAnchor, RuleFile, write_all
from scrinium.application.example import git, seed_repository
from scrinium.application.knowledge.doc_conflicts import (
    DocConflict,
    KnownValues,
    doc_conflicts,
    enclosing_code,
)
from scrinium.infrastructure.decisions import anchor_for, parse_decisions

DEFAULT_DIR: Final = "scrinium-demo"
BRANCH: Final = "campana-verano"
_REVIEWER: Final = "Ana Ejemplo"
_VERIFIED_AT: Final = "2026-03-03T10:00:00Z"
_RULES_COMMIT_DATE: Final = "2026-03-03T11:00:00+01:00"
_CHANGE_DATE: Final = "2026-06-01T09:00:00+02:00"


@dataclass(frozen=True)
class _Example:
    id: str
    title: str
    statement: str
    path: str
    # A piece of the line the rule decides on; the decision around it is the anchor.
    marker: str
    when: tuple[str, ...]
    then: tuple[str, ...]
    why: str


# Fixed ids: the demo writes the same files on every machine.
_RULES: Final = (
    _Example(
        "01950000-0000-7000-8000-000000000001",
        "Envío gratis desde 40 EUR en la península",
        "Los pedidos a la península de 40 EUR o más no pagan envío.",
        "tienda/envios.py", "if subtotal >= ENVIO_GRATIS_DESDE",
        ("el destino no son las islas", "el subtotal es de 40 EUR o más"),
        ("el envío cuesta 0 EUR",),
        "Campaña de primavera de 2026.",
    ),
    _Example(
        "01950000-0000-7000-8000-000000000002",
        "Como mucho 10 unidades de un producto por pedido",
        "Un pedido no reserva más de 10 unidades de un mismo producto.",
        "tienda/stock.py", "if pedidas > MAXIMO_POR_PEDIDO",
        ("se piden más de 10 unidades de un producto",),
        ("el pedido se rechaza",),
        "Evita que un solo cliente agote el stock.",
    ),
    _Example(
        "01950000-0000-7000-8000-000000000004",
        "Los descuentos nunca pasan del 25 % del subtotal",
        "Los descuentos sumados de un pedido nunca pasan del 25 % de su subtotal.",
        "tienda/descuentos.py", "min(porcentaje, DESCUENTO_MAXIMO)",
        ("hay descuentos de cliente y de cupón",), ("se aplica como mucho un 25 %",),
        "Margen mínimo de la tienda.",
    ),
    _Example(
        "01950000-0000-7000-8000-000000000003",
        "Un cupón solo vale en pedidos de 30 EUR o más",
        "Un cupón de descuento solo se aplica si el subtotal es de 30 EUR o más.",
        "tienda/descuentos.py", "if cupon is not None and subtotal >= PEDIDO_MINIMO_CUPON",
        ("hay cupón", "el subtotal es de 30 EUR o más"),
        ("se suma el descuento del cupón",),
        "Los cupones no deben usarse en pedidos pequeños.",
    ),
)  # fmt: skip


def _rule(repo: Path, example: _Example) -> RuleFile:
    text = (repo / example.path).read_text(encoding="utf-8")
    line = next(n for n, row in enumerate(text.splitlines(), 1) if example.marker in row)
    decision = anchor_for(parse_decisions(example.path, text), line, line)
    if decision is None:
        raise RuntimeError(f"no decision in {example.path}:{line}")
    anchor = FileAnchor(
        example.path, decision.symbol, decision.kind, decision.start_line, decision.end_line,
        decision.shape, decision.value, decision.sketch,
    )  # fmt: skip
    return RuleFile(
        id=example.id, title=example.title, statement=example.statement,
        verified_by=_REVIEWER, verified_at=_VERIFIED_AT, anchors=(anchor,),
        when=example.when, then=example.then, why=example.why,
        extractor="scrinium demo", model=None,
    )  # fmt: skip


def prepare(repo: Path) -> list[RuleFile]:
    """The example with its verified rules on `main`, and the campaign change on a branch."""
    seed_repository(repo)
    rules = [_rule(repo, example) for example in _RULES]
    write_all(repo / RULES_DIR, rules)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Reglas verificadas", date=_RULES_COMMIT_DATE)
    git(repo, "checkout", "-q", "-b", BRANCH)
    shipping = repo / "tienda/envios.py"
    shipping.write_text(
        shipping.read_text(encoding="utf-8").replace('Decimal("40")', 'Decimal("60")'),
        encoding="utf-8",
    )
    git(repo, "commit", "-q", "-am", "Envio gratis desde 60 EUR en verano", date=_CHANGE_DATE)
    return rules


def _step(number: int, text: str) -> None:
    print(f"\n{number}. {text}", flush=True)


def demo(folder: Path) -> int:
    repo = folder.resolve()
    if repo.exists() and any(repo.iterdir()):
        print(
            f"{repo} already exists and is not empty. Delete it or choose another folder: "
            "scrinium demo --dir OTHER",
            file=sys.stderr,
        )
        return 2
    _step(1, f"Cloning the example shop into {repo}")
    rules = prepare(repo)
    print("   A Git repository with orders, discounts, shipping and stock.")

    _step(2, f"The rules the team already reviewed, in {RULES_DIR}/:")
    for rule in rules:
        anchor = rule.anchors[0]
        print(f"   - {rule.title}  ({anchor.path}:{anchor.start_line})")

    _step(3, f"On the branch {BRANCH}, someone moves free shipping from 40 to 60 EUR:")
    print('   tienda/envios.py: ENVIO_GRATIS_DESDE = Decimal("60")')

    _step(4, f"scrinium check --base main  (on the branch {BRANCH})")
    result = check(Repository.at(repo), "main")
    print()
    print(to_markdown(result))
    code = 2 if result.errors else 1 if result.failed else 0
    verdict = {
        0: "The check passes.",
        1: "The check fails (code 1): a PR like this is not merged until someone reviews the rule.",
        2: "The check could not run (code 2).",
    }[code]
    print(verdict)

    _step(5, "What the documents say against the code (on main):")
    conflicts = _doc_conflicts(repo, rules)
    for conflict in conflicts:
        print(f"   - {conflict.document}:{conflict.line}: «{conflict.sentence}»")
        print(f"     says {', '.join(conflict.says)}; the rule «{conflict.title}» decides with "
              f"{', '.join(conflict.code)} ({conflict.place})")  # fmt: skip
    if not conflicts:
        print("   No document contradicts the code.")
    print(
        "\nNext:\n"
        f"  - See the rules in the console: scrinium start, and add {repo} as a source.\n"
        f"  - Protect your repository: scrinium hooks install, or scrinium check in your CI.\n"
        "  - None of this used a model, the GPU or the network."
    )
    return 0 if code == 1 else code


def _doc_conflicts(repo: Path, rules: list[RuleFile]) -> list[DocConflict]:
    """The example's policy document against the rules' decisions on `main` (phase 8.2)."""
    repository = Repository.at(repo)
    reader = Reader(repository, repository.resolve("main"))
    known = []
    for rule in rules:
        anchor = rule.anchors[0]
        text = reader.read(anchor.path) or ""
        found = anchor_for(parse_decisions(anchor.path, text), anchor.start_line, anchor.end_line)
        if found is not None:
            known.append(
                KnownValues(
                    rule.id,
                    rule.title or "",
                    rule.statement,
                    found.literals,
                    f"{anchor.path}:{found.start_line}",
                    enclosing_code(anchor.path, text, found),
                )
            )
    documents = [("docs/politicas.md", reader.read("docs/politicas.md") or "")]
    return doc_conflicts(known, documents)
