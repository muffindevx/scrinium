"""F10: the filter counts domain decisions, whatever the domain, and subtracts drawing."""

import uuid

from scrinium.application.analysis.candidates import (
    DEFAULT_MIN_SCORE,
    Candidate,
    decision_groups,
    decision_windows,
    is_visual,
    score_text,
)
from scrinium.domain.paths import is_test_path


def test_a_game_rule_passes_without_business_vocabulary() -> None:
    # No errors, money or permissions: a rule from a game.
    door = (
        "export function rollDoor(state, random) {\n"
        "  if (random() < TRICK_CHANCE) return pick(TRICKS, random);\n"
        "  const rare = state.raresLeft > 0 && random() < RARE_CHANCE;\n"
        "  if (!rare) return 'plain';\n"
        "}"
    )
    assert score_text(door)[0] >= DEFAULT_MIN_SCORE


def test_business_rules_still_pass() -> None:
    rejection = "def recargo(importe):\n    if importe < 0:\n        raise ValueError('neg')\n"
    tax = "def total(subtotal, customer):\n    return round(subtotal * (1 + TAX_RATE), 2)\n"
    assert score_text(rejection)[0] >= DEFAULT_MIN_SCORE
    assert score_text(tax)[0] >= DEFAULT_MIN_SCORE


def test_business_guards_and_database_rules_pass() -> None:
    """Q1: how a business system decides, unlike a game."""
    guard = (
        "async resolveActor(session) {\n"
        "  const user = await usersRepository.findByEmail(session.user.email);\n"
        "  if (!user || !user.isActive) return { kind: 'forbidden' };\n"
        "  return { kind: 'ok', actor: user };\n"
        "}"
    )
    last_admin = "const admins = users.some((u) => u.role === 'ADMIN' && u.isActive);\n"
    sql_guard = (
        "CREATE OR REPLACE FUNCTION grc.fn_soa_version_guard()\n"
        "RETURNS trigger LANGUAGE plpgsql AS $$\nBEGIN\n"
        "    IF OLD.status <> 'DRAFT' THEN\n"
        "        RAISE EXCEPTION 'Solo se eliminan SoA en borrador';\n"
        "    END IF;\n    RETURN OLD;\nEND $$;"
    )
    schema = "    check('ck_app_users_role', sql`${t.role} in ('VIEWER', 'EDITOR', 'ADMIN')`),\n"
    permissions = "  EDITOR: ['grc:read', 'grc:write', 'soa:approve'],\n"
    for text in (guard, last_admin, sql_guard, schema, permissions):
        assert score_text(text)[0] >= 1, text


def test_a_games_state_checks_do_not_all_become_decisions() -> None:
    # Lower-case kinds are everywhere in a game: alone they are not a business status.
    line = "      if (level.rules.kind === 'horde') updateHorde(delta);\n"
    assert score_text(line)[0] == 0


def test_code_without_decisions_is_not_a_candidate() -> None:
    plumbing = "def wire(app):\n    app.register(routes)\n    app.start()\n"
    score, reasons = score_text(plumbing)
    assert score == 0 and "sin decisiones" in reasons


def test_drawing_code_loses_points() -> None:
    drawing = (
        "function drawCone(g, npc) {\n"
        "  if (npc.range > 0) g.circle(npc.x, npc.y, npc.range);\n"
        "  g.fill(PALETTE.cone);\n"
        "  g.stroke({ width: 2 });\n"
        "  sprite.position.set(npc.x, npc.y);\n"
        "}"
    )
    # The only condition draws: it does not count as a decision.
    assert score_text(drawing)[0] < DEFAULT_MIN_SCORE
    mixed = drawing.replace("g.fill(PALETTE.cone);", "if (npc.alert >= ALERT_MAX) npc.chase();")
    score, reasons = score_text(mixed)
    assert "mas dibujo que decisiones" in reasons and score < DEFAULT_MIN_SCORE


def test_a_long_part_with_few_decisions_still_passes() -> None:
    # Filler does not reach the model (windows go), so it is not penalized: only noted.
    head = "def regla(valor):\n    if valor > 10:\n        raise ValueError('limite')\n"
    score, reasons = score_text(head + "    valor.append(1)\n" * 60)
    assert "pocas decisiones para su tamano" in reasons and score >= DEFAULT_MIN_SCORE


def test_a_long_function_goes_as_windows_around_its_decisions() -> None:
    body = ["function start() {"] + ["  draw();"] * 30
    body += ["  if (lives <= 0) finish('caught');"] + ["  draw();"] * 30 + ["}"]
    whole = Candidate(uuid.uuid4(), "src/main.ts", 100, 100 + len(body) - 1, "\n".join(body), 5, [])
    windows = decision_windows(whole)
    assert len(windows) == 1
    window = windows[0]
    # The lines are still the file's: the decision is on line 131.
    assert window.start_line <= 131 <= window.end_line
    assert window.end_line - window.start_line == 12
    assert window.text.split("\n")[131 - window.start_line] == "  if (lives <= 0) finish('caught');"


def test_visual_code_is_recognised_by_path_or_name() -> None:
    assert is_visual("src/screens/menu.ts")
    assert is_visual("src/game/hudpanel.ts")
    assert is_visual("src/game/world.ts", "drawTiles")
    assert not is_visual("src/billing/invoice.py", "calculate_total")
    # A backend's "views" usually has permissions and validations: it does not count as visual.
    assert not is_visual("app/views/orders.py", "approve")


def test_close_decisions_are_one_group_and_a_part_without_any_is_one_whole_group() -> None:
    from scrinium.application.analysis.candidates import decision_groups

    text = "\n".join([
        "function f(x) {",
        "  if (x > MAX_X) return 1;",   # 1
        "  else if (x < MIN_X) return 2;",  # 2: 1 line away, same group
        "  a();", "  b();", "  c();", "  d();",
        "  if (x === LIMIT_X) return 3;",  # 7: 5 lines away, another group
        "}",
    ])  # fmt: skip
    assert decision_groups(text) == [(1, 2), (7, 7)]
    assert decision_groups("wire();\nstart();") == [(0, 1)]


def test_test_paths_are_recognised_in_every_layout() -> None:
    tests = [
        "tests/test_pagos.py",
        "src/app/__tests__/cart.ts",
        "web/billing.spec.ts",
        "web/Button.stories.tsx",
        "pkg/billing_test.go",
        "src/main/java/BillingTest.java",
        "Billing.Tests/InvoiceTests.cs",
        "spec/models/user_spec.rb",
        "app/fixtures/users.json",
    ]
    code = ["src/billing.py", "src/testing_mode.py", "src/contest/rules.ts", "lib/specs.rb"]
    assert all(is_test_path(path) for path in tests)
    assert not any(is_test_path(path) for path in code)


def test_a_constraint_opened_on_its_own_line_is_cited_whole() -> None:
    schema = (
        "  (t) => [\n"
        "    check(\n"
        "      'ck_document_validity',\n"
        "      sql`${t.validUntil} is null or ${t.validUntil} >= ${t.validFrom}`,\n"
        "    ),\n"
        "  ],\n"
    )
    assert decision_groups(schema) == [(1, 4)]


def test_criticality_is_proposed_high_for_permissions_security_and_money() -> None:
    """F7: fixed rules, never the model's guess; a person confirms it."""
    from scrinium.application.analysis.extraction import proposed_criticality
    from scrinium.domain.enums import Criticality, RuleCategory

    assert proposed_criticality(RuleCategory.AUTHORIZATION, "if (!user.isActive)") is (
        Criticality.HIGH
    )
    assert proposed_criticality(RuleCategory.SECURITY_CONSTRAINT, "x") is Criticality.HIGH
    assert proposed_criticality(RuleCategory.CALCULATION, "total = subtotal * TAX_RATE") is (
        Criticality.HIGH
    )
    assert proposed_criticality(RuleCategory.VALIDATION, "if (lives <= 0)") is Criticality.MEDIUM
