"""B4: which proposals the inbox groups for one review."""

import uuid
from typing import Any

from scrinium.application.knowledge import inbox
from scrinium.domain.enums import Criticality, VerificationStatus, VersionOrigin
from scrinium.infrastructure.sqlite.models import EvidenceRecord


def _rule(statement: str, path: str, shape: str | None = None) -> Any:
    rule = inbox._Rule(
        entity_id=uuid.uuid4(), review_reason=None, version_id=uuid.uuid4(),
        status=VerificationStatus.UNVERIFIED, origin=VersionOrigin.SOURCE_EXTRACTION,
        title=statement[:40], statement=statement, criticality=Criticality.MEDIUM,
        shape=shape,
    )  # fmt: skip
    rule.citation = EvidenceRecord(locator={"path": path})
    return rule


def _together(*rules: Any) -> bool:
    groups = inbox._groups(list(rules))
    return len({groups.find(rule.entity_id) for rule in rules}) == 1


def test_the_same_values_about_the_same_subject_group_across_files() -> None:
    schema = _rule(
        "El estado de un nodo de agente debe ser 'running', 'succeeded' o 'failed'.",
        "packages/db/src/schema.ts",
    )
    migration = _rule(
        "El estado de los nodos de agente debe estar limitado a 'running', 'succeeded' o 'failed'.",
        "packages/db/drizzle/0000_init.sql",
    )
    assert _together(schema, migration)


def test_the_same_values_about_another_subject_stay_apart() -> None:
    nodes = _rule(
        "El estado de un nodo de agente debe ser 'running', 'succeeded' o 'failed'.",
        "packages/db/src/schema.ts",
    )
    runs = _rule(
        "El estado de una ejecucion debe estar limitado a 'running', 'succeeded' o 'failed'.",
        "packages/db/drizzle/0000_init.sql",
    )
    assert not _together(nodes, runs)


def test_one_decision_shape_and_value_groups_only_what_says_the_same() -> None:
    # `x > 0` in two places: the same decision to the parser, two different rules.
    cost = _rule("Si hay coste, se muestra junto a la ejecucion.", "ui/run.tsx", "gt:0")
    ttl = _rule("La sesion dura por defecto 30 dias.", "api/config.ts", "gt:0")
    assert not _together(cost, ttl)
    sync = _rule("El intervalo de envio debe ser de 0.01 segundos o mas.", "sdk/_sender.py", "gt:0")
    twin = _rule(
        "El intervalo de envio debe ser de 0.01 segundos o mas.", "sdk/_async_sender.py", "gt:0"
    )
    assert _together(sync, twin)
