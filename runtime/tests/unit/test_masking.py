"""Masking before a remote request (ADR-010): what is replaced, what is not, and that the
answer gets the real values back without breaking its JSON."""

import json

from scrinium.application.ai.masking import Masker

CODE = """def notify(user):
    send("ana.garcia@example.com", "Su factura está lista, revise el portal de clientes")
    db = connect("postgres://admin:s3creta@db.interno:5432/facturas")
    call("+34 600 123 456")  # soporte
    allow("10.20.30.40")
    return calculate_invoice_total(user, 1234567, "v1.2")
"""


def test_personal_data_becomes_stable_placeholders() -> None:
    masker = Masker(personal=True, literals=False)
    first = masker.mask(CODE)
    second = masker.mask("Escribe a ana.garcia@example.com si falla.")
    for value in ("ana.garcia@example.com", "admin:s3creta", "600 123 456", "10.20.30.40"):
        assert value not in first
    # The same value is the same placeholder in every message of the request.
    assert "<EMAIL_1>" in first and "<EMAIL_1>" in second
    assert all(p in first for p in ("<URL_1>", "<PHONE_1>", "<IP_1>"))
    # Identifiers, plain numbers, versions and short literals stay: rules are about them.
    assert "calculate_invoice_total" in first and "1234567" in first and '"v1.2"' in first
    # Long literals stay unless asked for.
    assert "Su factura está lista" in first
    assert masker.masked == 4


def test_long_literals_are_masked_only_when_asked() -> None:
    masker = Masker(personal=False, literals=True)
    masked = masker.mask(CODE)
    assert "Su factura está lista" not in masked and '"<TEXT_' in masked
    assert '"v1.2"' in masked


def test_the_answer_gets_the_real_values_back_as_valid_json() -> None:
    masker = Masker(personal=True, literals=True)
    masker.mask(CODE)
    answer = json.dumps(
        {"summary": 'Avisa a <EMAIL_1> con "<TEXT_1>"', "quote": ["llama al <PHONE_1>"]}
    )
    restored = json.loads(masker.unmask(answer))
    assert restored["summary"].startswith("Avisa a ana.garcia@example.com con")
    assert "Su factura está lista" in restored["summary"]
    assert restored["quote"] == ["llama al +34 600 123 456"]
    # Plain text answers too, and an unknown placeholder is left as it is.
    assert masker.unmask("Ver <IP_1> y <IP_9>") == "Ver 10.20.30.40 y <IP_9>"


def test_nothing_is_masked_when_off() -> None:
    masker = Masker(personal=False, literals=False)
    assert masker.mask(CODE) == CODE and masker.masked == 0
