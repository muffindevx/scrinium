"""F3: a rule's statement is split into when it applies and what it demands."""

from scrinium.application.knowledge.content import shaped, split_statement


def test_a_condition_first_statement_splits_at_its_first_comma() -> None:
    assert split_statement("Si las vidas llegan a 0, la partida termina.") == (
        "Si las vidas llegan a 0",
        "La partida termina.",
    )
    assert split_statement("Cuando la SoA está aprobada, sus ítems no se pueden mover.") == (
        "Cuando la SoA está aprobada",
        "Sus ítems no se pueden mover.",
    )


def test_commas_inside_parentheses_or_quotes_do_not_split() -> None:
    text = "Si el rol (VIEWER, EDITOR) no tiene 'grc:write, soa:approve', se rechaza con 403."
    when, then = split_statement(text)
    assert when == "Si el rol (VIEWER, EDITOR) no tiene 'grc:write, soa:approve'"
    assert then == "Se rechaza con 403."


def test_a_rule_that_always_holds_has_no_condition() -> None:
    text = "La bolsa tiene 3 huecos."
    assert split_statement(text) == (None, text)
    assert split_statement("Si nada más") == (None, "Si nada más")


def test_shaped_fills_the_rule_fields() -> None:
    shape = shaped("Si las vidas llegan a 0, la partida termina.")
    assert shape["trigger_conditions"] == [{"description": "Si las vidas llegan a 0"}]
    assert shape["expected_behavior"] == [{"description": "La partida termina."}]
    assert shaped("La bolsa tiene 3 huecos.")["trigger_conditions"] == []
    long = "Si " + "a" * 1200 + ", b."
    assert shaped(long)["trigger_conditions"] == []
