"""Structural map (tree-sitter) of a real synced repository."""

import uuid
from pathlib import Path

from sqlalchemy import select

from scrinium.infrastructure.sqlite.models import CodeSymbol, SourceItem, SourceRevision
from tests.conftest import Runtime
from tests.integration.test_analyses import Api
from tests.integration.test_ingestion import commit_all, git, write

BILLING = """\
class Invoice:
    def calculate_total(self, lines):
        # El total redondea a dos decimales.
        return round(sum(line.amount for line in lines), 2)


def apply_tax(amount):
    return amount * 1.21
"""
CART = """\
export class Cart {
  total(items: Item[]): number {
    return sum(items)
  }
}
export const discount = (price: number) => (price > 100 ? applyDiscount(price) : price)
"""


def _symbols(runtime: Runtime, path: str, current: bool = True) -> dict[str, CodeSymbol]:
    with runtime.database.session() as session:
        query = (
            select(CodeSymbol)
            .join(SourceRevision, SourceRevision.id == CodeSymbol.source_revision_id)
            .where(SourceRevision.metadata_["path"].as_string() == path)
        )
        if current:
            query = query.join(SourceItem, SourceItem.current_revision_id == SourceRevision.id)
        rows = session.scalars(query).all()
        session.expunge_all()
        return {symbol.qualified_name: symbol for symbol in rows}


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "src/billing.py", BILLING)
    write(root, "web/cart.ts", CART)
    write(root, "docs/notas.md", "# Notas\n")
    commit_all(root, "inicio")
    return root


def test_sync_maps_functions_and_tells_trivial_from_real_changes(
    runtime: Runtime, tmp_path: Path
) -> None:
    root = _repo(tmp_path)
    api = Api(runtime, root)

    billing = _symbols(runtime, "src/billing.py")
    assert set(billing) == {"Invoice", "Invoice.calculate_total", "apply_tax"}
    total = billing["Invoice.calculate_total"]
    assert (total.kind, total.start_line, total.end_line) == ("method", 2, 4)
    assert set(total.calls) == {"round", "sum"}
    assert set(_symbols(runtime, "web/cart.ts")) == {"Cart", "Cart.total", "discount"}
    assert _symbols(runtime, "docs/notas.md") == {}

    # Only a comment and whitespace: the logical hash does not change.
    write(
        root,
        "src/billing.py",
        BILLING.replace("# El total", "# Total final, ").replace("return round(", "return  round("),
    )
    commit_all(root, "formato")
    api.sync()
    trivial = _symbols(runtime, "src/billing.py")["Invoice.calculate_total"]
    assert trivial.content_hash != total.content_hash
    assert trivial.logic_hash == total.logic_hash

    # Otro redondeo: cambio real.
    write(root, "src/billing.py", BILLING.replace(", 2)", ", 3)"))
    commit_all(root, "redondeo")
    api.sync()
    real = _symbols(runtime, "src/billing.py")["Invoice.calculate_total"]
    assert real.logic_hash != total.logic_hash
    assert (
        _symbols(runtime, "src/billing.py")["apply_tax"].logic_hash
        == billing["apply_tax"].logic_hash
    )
    # Earlier revisions keep their symbols: they are the history.
    with runtime.database.session() as session:
        versions = session.scalars(
            select(CodeSymbol.logic_hash).where(
                CodeSymbol.qualified_name == "Invoice.calculate_total"
            )
        ).all()
    assert len(versions) == 3 and len(set(versions)) == 2

    # Rebuilding regenerates; deleting the source deletes them all.
    api.post("/api/v1/index/rebuild")
    assert set(_symbols(runtime, "src/billing.py")) == set(billing)
    response = runtime.client.delete(f"/api/v1/sources/{api.source_id}", headers=api.headers)
    assert response.status_code == 204
    with runtime.database.session() as session:
        assert session.scalars(select(CodeSymbol)).all() == []


def test_search_finds_functions_by_name_in_any_style(runtime: Runtime, tmp_path: Path) -> None:
    api = Api(runtime, _repo(tmp_path))
    for query in ("calculateTotal", "calculate_total", "CalculateTotal"):
        found = runtime.client.get(
            f"/api/v1/projects/{api.project_id}/search", params={"q": query}
        ).json()["results"]
        assert found, query
        assert found[0]["path"] == "src/billing.py", query
        assert "symbol" in found[0]["matched_by"], query
    other = runtime.client.post("/api/v1/projects", json={"name": "Otro"}, headers=api.headers)
    hidden = runtime.client.get(
        f"/api/v1/projects/{other.json()['id']}/search", params={"q": "calculate_total"}
    ).json()["results"]
    assert hidden == []
    assert uuid.UUID(api.project_id)
