"""F1/F2: deterministic checks of an extracted rule, and llama-server timings."""

from scrinium.application.analysis import rule_checks
from scrinium.infrastructure.llm.openai_compatible import _timings


def test_a_title_at_the_schema_limit_counts_as_cut() -> None:
    assert rule_checks.is_truncated("x" * 100, 100)
    assert not rule_checks.is_truncated("Limite de intentos", 100)


def test_a_title_cut_short_of_the_limit_counts_as_cut() -> None:
    # B1: cut at the last whole word, it no longer reaches the cap.
    assert rule_checks.is_truncated("El estado de un nodo de agente debe ser uno de los", 60)
    assert rule_checks.is_truncated("Los eventos deben ser de los definidos en el", 60)
    assert rule_checks.is_truncated("Limite de reintentos del envio de…", 60)
    assert not rule_checks.is_truncated("El estado de un nodo debe ser conocido", 60)


def test_language_by_frequent_words() -> None:
    assert rule_checks.language("La cuenta se bloquea tras 5 intentos fallidos") == "es"
    assert rule_checks.language("Candies must not be placed within the radius") == "en"
    assert rule_checks.language("HUD") == "?"


def test_visual_vocabulary_in_both_languages() -> None:
    assert rule_checks.mentions_visual("Dibujo del cono de vision")
    assert rule_checks.mentions_visual("Carga de fuentes de HUD antes de mostrar texto")
    assert rule_checks.mentions_visual("Render the minimap")
    assert not rule_checks.mentions_visual("El IVA no se aplica a clientes exentos")


def test_evidence_needs_a_condition_comparison_or_constant() -> None:
    assert rule_checks.has_evidence("if (lives <= 0) finish('caught');")
    assert rule_checks.has_evidence("const MIN_DISTANCE_TILES = 12;")
    assert not rule_checks.has_evidence("player.respawn();\nplayer.spotlight();")


def test_cited_text_uses_file_line_numbers() -> None:
    text = "a\nb\nc\nd"
    assert rule_checks.cited_text(text, 10, 11, 12) == "b\nc"


def test_llama_server_timings_split_reading_from_writing() -> None:
    result = {"timings": {"prompt_ms": 7012.5, "predicted_ms": 10400.1, "draft_n": 120,
                          "draft_n_accepted": 30, "cache_n": 528}}  # fmt: skip
    assert _timings(result) == {
        "prompt_ms": 7012,
        "generation_ms": 10400,
        "draft_tokens": 120,
        "draft_accepted": 30,
    }
    assert _timings({}) == {}


_PROJECTOR = """\
switch (event.type) {
  case "agent.started":
    if (!terminal) {
      state.status = "running";
    }
    break;
  case "agent.finished":
    if (!terminal) {
      state.status = "succeeded";
    }
    break;
}""".splitlines()


def test_a_literal_from_the_next_case_is_not_anchored() -> None:
    # B3: line 4 runs on agent.started; agent.finished is the sibling case below it.
    reach = rule_checks.decision_reach(_PROJECTOR, 4, 4)
    said = "Si no es terminal, el estado pasa a 'running' con el evento 'agent.finished'."
    assert rule_checks.unanchored_literals(said, reach) == ["agent.finished"]
    right = "Si no es terminal, el estado pasa a 'running' con el evento agent.started."
    assert rule_checks.unanchored_literals(right, reach) == []
    # Line 9 belongs to agent.finished, written above it.
    reach = rule_checks.decision_reach(_PROJECTOR, 9, 9)
    assert rule_checks.unanchored_literals("Con 'agent.finished' pasa a 'succeeded'.", reach) == []


def test_a_decision_reaches_its_own_branch_below() -> None:
    lines = ["if (depth > 6) {", '  return "[truncated]";', "}"]
    reach = rule_checks.decision_reach(lines, 1, 1)
    assert rule_checks.unanchored_literals("Pasado 6 niveles devuelve '[truncated]'.", reach) == []


def test_quoted_phrases_and_abbreviations_are_words_not_literals() -> None:
    said = 'Un pedido "en borrador" no se factura, p.ej. tras cancelarlo; ver `sale.order`.'
    assert rule_checks.unanchored_literals(said, "state == 'draft'") == []
