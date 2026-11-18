"""Documents against the code (phase 8.2): what must be found, and what must not.

The second half are the false positives a real repository produced (tables of results, dates,
ADR numbers, routes, identifiers, numbers against a rule that decides with names).
"""

from scrinium.application.knowledge.doc_conflicts import KnownValues, doc_conflicts

SHIPPING = KnownValues(
    "r1", "Envío gratis desde 40 EUR", "El envío es gratis en pedidos de 40 EUR o más.",
    ("40",), "src/envios.py:3",
)  # fmt: skip
DISCOUNT = KnownValues(
    "r2", "Tope de descuento", "El descuento no puede superar el 25 % del pedido.",
    ("0.25",), "src/descuentos.py:8",
)  # fmt: skip
ITEMS = KnownValues(
    "r3", "Order item limit", "An order cannot have more than 50 items.", ("50",), "src/o.py:4"
)
TERMINAL = KnownValues(
    "r4", "Terminal node status", "A node status is terminal when it is 'succeeded' or 'failed'.",
    ("succeeded", "failed"), "src/nodes.ts:12",
)  # fmt: skip
BATCH = KnownValues(
    "r5", "Tamaño máximo de lote de eventos",
    "El lote de eventos debe contener entre 1 y MAX_BATCH_EVENTS eventos.", ("1", "500"),
    "src/events.ts:95",
)  # fmt: skip
RULES = [SHIPPING, DISCOUNT, ITEMS, TERMINAL, BATCH]


def _found(document: str, path: str = "docs/manual.md") -> list[tuple[str, tuple[str, ...]]]:
    return [(c.rule_id, c.says) for c in doc_conflicts(RULES, [(path, document)])]


def test_real_disagreements_are_found() -> None:
    assert _found("El envío es gratis en pedidos de más de 50 EUR.") == [("r1", ("50",))]
    assert _found("El descuento máximo es del 30 % del pedido.") == [("r2", ("30 %",))]
    assert _found("An order with more than `100` items is rejected.") == [("r3", ("100",))]
    assert _found("A terminal node status is `done` or `error`.") == [("r4", ("done", "error"))]
    # A sentence wrapped over two lines keeps its first line.
    [conflict] = doc_conflicts(
        RULES, [("docs/m.md", "Texto.\n\nEl envío es gratis\nen pedidos de 60 EUR.\n")]
    )
    assert conflict.line == 3


def test_agreement_and_partial_agreement_are_left_alone() -> None:
    assert _found("El envío es gratis en pedidos de 40 EUR.") == []
    assert _found("El descuento máximo es del 25 % del pedido.") == []
    # It says more than the rule (a third status): one of its names is the code's.
    assert _found("A terminal node status is `succeeded`, `failed` or `canceled`.") == []


def test_tables_headings_and_code_are_not_prose() -> None:
    assert _found("| Envío gratis | pedidos | 50 EUR |\n| --- | --- | --- |\n") == []
    assert _found("## Envío gratis desde 50 EUR en pedidos\n") == []
    assert _found("```\nenvío gratis pedidos 50\n```\n") == []
    assert _found("Ejemplo:\n\n    envío gratis en pedidos de 50 EUR\n") == []


def test_run_outputs_are_not_policies() -> None:
    sentence = "El envío es gratis en pedidos de más de 50 EUR."
    assert _found(sentence, "evals/narrator/results/latest.md") == []
    assert _found(sentence, "evals/2026-11-17T20-37-44-470Z-ollama.md") == []
    assert _found(sentence, "CHANGELOG.md") == []


def test_what_only_looks_like_a_quantity_is_ignored() -> None:
    assert _found("Desde 2026-11-15 el envío es gratis en pedidos grandes.") == []
    assert _found("Ver ADR-0003 sobre el envío gratis en pedidos.") == []
    assert _found("El envío gratis de pedidos lo calcula /v2/shipping/free.") == []
    assert _found("El envío gratis en pedidos usa el modelo qwen3 y la versión v1.2.3.") == []
    assert _found("Commit 7462a22 cambió el envío gratis de los pedidos.") == []


def test_like_is_compared_with_like() -> None:
    # The rule decides with names: the sentence's numbers say nothing against it.
    assert _found("A terminal node status is reached after 3 retries.") == []
    # The rule decides with numbers: a quoted name in the sentence is not a disagreement.
    assert _found("El envío es gratis en pedidos con `free_shipping` activo.") == []


def test_quoted_identifiers_do_not_make_a_rule_about_them() -> None:
    # MAX_BATCH_EVENTS in the statement must not make "batch events" sentences about this rule.
    assert _found("Batch events are retried 7 times by the worker.") == []
