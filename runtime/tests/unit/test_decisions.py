"""Decision nodes and their fingerprints (ADR-011), without a database."""

import pytest

from scrinium.infrastructure.decisions import Kind, anchor_for, parse_decisions

PAY = """MAX = 100


def pay(order, user):
    if order.amount > MAX and user.vip:
        raise UserError("too much")
    order.state = 'sale'
    return order.amount * 0.21
"""


def _by_kind(path: str, text: str) -> dict[str, tuple[str, str]]:
    return {d.kind: (d.shape, d.value) for d in parse_decisions(path, text)}


def test_finds_each_kind_with_its_symbol() -> None:
    found = {(d.kind, d.start_line, d.end_line, d.symbol) for d in parse_decisions("p.py", PAY)}
    assert (Kind.GUARD, 5, 6, "pay") in found
    assert (Kind.THRESHOLD, 5, 5, "pay") in found
    assert (Kind.STATE_CHANGE, 7, 7, "pay") in found
    assert (Kind.FORMULA, 8, 8, "pay") in found


@pytest.mark.parametrize(
    "edited",
    [
        # Locals renamed, operands of `and` swapped, `>` written as `<`, a comment and new lines.
        PAY.replace("order, user", "o, customer")
        .replace(
            "if order.amount > MAX and user.vip:",
            "if (customer.vip\n        and MAX < o.amount):  # x",
        )
        .replace("order.state", "o.state")
        .replace("return order.amount", "return o.amount"),
        # The whole function moved down.
        "\n\n\n" + PAY,
    ],
)
def test_cosmetic_edits_keep_both_fingerprints(edited: str) -> None:
    assert _by_kind("p.py", edited) == _by_kind("p.py", PAY)


def test_a_new_threshold_changes_the_value_only() -> None:
    before = _by_kind("p.py", PAY)
    after = _by_kind("p.py", PAY.replace("MAX = 100", "MAX = 150"))
    assert after[Kind.GUARD][0] == before[Kind.GUARD][0]
    assert after[Kind.GUARD][1] != before[Kind.GUARD][1]


def test_typescript_guard_threshold_and_validation() -> None:
    text = (
        "const LIMIT = 3;\nexport function f(a: number) {\n"
        "  if (a >= LIMIT) { throw new Error('x') }\n"
        "  const s = z.string().min(3).max(20);\n}\n"
    )
    kinds = {(d.kind, d.start_line) for d in parse_decisions("f.ts", text)}
    assert {(Kind.GUARD, 3), (Kind.THRESHOLD, 3), (Kind.VALIDATION, 4)} <= kinds


def test_sql_guard_and_state_change() -> None:
    text = (
        "BEGIN\n    IF OLD.status <> 'DRAFT' THEN\n        RAISE EXCEPTION 'no %', v_x;\n"
        "    END IF;\n    UPDATE t SET status = 'X'\n    WHERE id = p_id;\n"
    )
    found = {(d.kind, d.start_line, d.end_line) for d in parse_decisions("f.sql", text)}
    assert found == {(Kind.GUARD, 2, 3), (Kind.STATE_CHANGE, 5, 6)}


def test_anchor_prefers_the_guard_over_what_it_contains() -> None:
    anchor = anchor_for(parse_decisions("p.py", PAY), 5, 6)
    assert anchor is not None and anchor.kind == Kind.GUARD
    assert anchor_for(parse_decisions("p.py", PAY), 1, 2) is None


def test_unsupported_languages_have_no_decisions() -> None:
    assert parse_decisions("notes.md", "if x > 3: raise") == []


def test_a_collection_of_values_keeps_its_shape_whatever_its_size() -> None:
    # Version 2: removing or swapping a state is the same decision with another value.
    base = "def f(e):\n    if e.state in {'approved', 'posted', 'paid'}:\n        raise E()\n"
    fewer = base.replace("'approved', ", "")
    swapped = base.replace("approved", "draft")
    reordered = base.replace("'approved', 'posted'", "'posted', 'approved'")

    def guard(text: str) -> tuple[str, str]:
        found = next(d for d in parse_decisions("a.py", text) if d.kind == "guard")
        return found.shape, found.value

    shapes = {guard(text)[0] for text in (base, fewer, swapped, reordered)}
    assert len(shapes) == 1
    assert len({guard(text)[1] for text in (base, fewer, swapped)}) == 3
    assert guard(reordered) == guard(base)  # A set has no order.
