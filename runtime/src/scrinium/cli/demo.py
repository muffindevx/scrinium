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
BRANCH: Final = "summer-campaign"
_REVIEWER: Final = "Ana Example"
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
        "Free shipping from 40 EUR on the mainland",
        "Mainland orders of 40 EUR or more pay no shipping.",
        "shop/shipping.py", "if subtotal >= FREE_SHIPPING_FROM",
        ("the destination is not the islands", "the subtotal is 40 EUR or more"),
        ("shipping costs 0 EUR",),
        "Spring 2026 campaign.",
    ),
    _Example(
        "01950000-0000-7000-8000-000000000002",
        "At most 10 units of a product per order",
        "An order never reserves more than 10 units of the same product.",
        "shop/stock.py", "if requested > MAXIMUM_PER_ORDER",
        ("more than 10 units of a product are requested",),
        ("the order is refused",),
        "Keeps a single customer from emptying the stock.",
    ),
    _Example(
        "01950000-0000-7000-8000-000000000004",
        "Discounts never exceed 25 % of the subtotal",
        "Discounts added together never exceed 25 % of the order's subtotal.",
        "shop/discounts.py", "min(percentage, MAXIMUM_DISCOUNT)",
        ("there are customer and coupon discounts",), ("at most 25 % is applied",),
        "The shop's minimum margin.",
    ),
    _Example(
        "01950000-0000-7000-8000-000000000003",
        "A coupon only applies to orders of 30 EUR or more",
        "A discount coupon only applies if the subtotal is 30 EUR or more.",
        "shop/discounts.py", "if coupon is not None and subtotal >= COUPON_MINIMUM_ORDER",
        ("there is a coupon", "the subtotal is 30 EUR or more"),
        ("the coupon discount is added",),
        "Coupons should not be used on small orders.",
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
    seed_repository(repo, english=True)
    rules = [_rule(repo, example) for example in _RULES]
    write_all(repo / RULES_DIR, rules)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Verified rules", date=_RULES_COMMIT_DATE)
    git(repo, "checkout", "-q", "-b", BRANCH)
    shipping = repo / "shop/shipping.py"
    shipping.write_text(
        shipping.read_text(encoding="utf-8").replace('Decimal("40")', 'Decimal("60")'),
        encoding="utf-8",
    )
    git(repo, "commit", "-q", "-am", "Free shipping from 60 EUR in summer", date=_CHANGE_DATE)
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
    print('   shop/shipping.py: FREE_SHIPPING_FROM = Decimal("60")')

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
        print(f'   - {conflict.document}:{conflict.line}: "{conflict.sentence}"')
        print(f"     says {', '.join(conflict.says)}; the rule \"{conflict.title}\" decides with "
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
    documents = [("docs/policies.md", reader.read("docs/policies.md") or "")]
    return doc_conflicts(known, documents)
