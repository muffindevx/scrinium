"""F10: the filter counts domain decisions, whatever the domain, and subtracts drawing."""

import uuid

from scrinium.application.analysis.candidates import (
    DEFAULT_MIN_SCORE,
    Candidate,
    decision_windows,
    is_visual,
    score_text,
)


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
