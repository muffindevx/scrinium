"""Two sources of one project may hold the same path: they never mix (ADR-011, phase 1.5)."""

import json
from pathlib import Path

from sqlalchemy import select

from scrinium.domain.enums import EntityType
from scrinium.infrastructure.sqlite.models import (
    CodeEdge,
    CodeSymbol,
    KnowledgeEntity,
    SourceItem,
    SourceRevision,
)
from tests.conftest import Runtime
from tests.integration.test_analyses import Api
from tests.integration.test_ingestion import commit_all, git, write

COMMISSION = """\
def comision(total):
    if total > 100:
        return total * 0.02
    return 0
"""
CALLER = "from src.pagos import comision\n\n\ndef cobrar(total):\n    return comision(total)\n"


def _repo(root: Path, with_caller: bool) -> Path:
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "src/pagos.py", COMMISSION)
    if with_caller:
        write(root, "src/cobros.py", CALLER)
    commit_all(root, "inicio")
    return root


def test_the_same_path_in_two_sources_stays_apart(runtime: Runtime, tmp_path: Path) -> None:
    ui = _repo(tmp_path / "ui", with_caller=True)
    api = Api(runtime, ui)
    api.post(
        f"/api/v1/projects/{api.project_id}/sources",
        {"kind": "git_repository", "path": str(_repo(tmp_path / "backend", with_caller=False))},
        201,
    )
    rule = {"t": "Comision por venta", "s": "Mas de 100 pagan un 2 %.", "c": "cal"}
    runtime.chat.reply = json.dumps({"d": [{"i": "D1", "r": rule}, {"i": "D2", "r": rule}]})
    detail = api.read(api.post(f"/api/v1/projects/{api.project_id}/extractions", {})["id"])
    assert detail["status"] == "completed", detail

    with runtime.database.session() as session:
        # Same title, same decision, two sources: two rules.
        rules = session.scalars(
            select(KnowledgeEntity).where(KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE)
        ).all()
        assert len(rules) == 2
        # The call in ui resolves to ui's function, not to the backend's copy.
        edge = session.scalars(select(CodeEdge).where(CodeEdge.callee == "comision")).one()
        assert edge.status == "resolved" and edge.to_symbol_id is not None
        target = session.get_one(CodeSymbol, edge.to_symbol_id)
        revision = session.get_one(SourceRevision, target.source_revision_id)
        item = session.get_one(SourceItem, revision.source_item_id)
        assert str(item.source_connection_id) == api.source_id

    # Each source is a root of its own in the architecture.
    top = runtime.client.get(f"/api/v1/projects/{api.project_id}/architecture").json()
    assert {m["name"] for m in top["modules"]} == {"ui", "backend"}
