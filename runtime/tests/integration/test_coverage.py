"""Coverage (phase 8.1): decisions per folder and file, with and without rules."""

from typing import Any

from tests.integration.test_analyses import Api
from tests.integration.test_rule_watch import _verify, api

__all__ = ["api"]


def _coverage(api: Api, path: str = "") -> Any:
    response = api.runtime.client.get(
        f"/api/v1/projects/{api.project_id}/coverage", params={"path": path}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_decisions_with_and_without_rules(api: Api) -> None:
    root = _coverage(api)
    [src] = root["children"]
    assert src["path"] == "src" and src["kind"] == "folder" and src["files"] == 2
    # pagos.py's threshold has the (unverified) commission rule; recargos.py's guard has none.
    assert src["unverified"] >= 1 and src["verified"] == 0 and src["uncovered"] >= 1
    assert root["layer_recall"]["inside_a_decision"] == 121

    files = {child["name"]: child for child in _coverage(api, "src")["children"]}
    assert files["pagos.py"]["uncovered"] == 0 and files["pagos.py"]["rules"] == 1
    assert files["recargos.py"]["rules"] == 0 and files["recargos.py"]["uncovered"] >= 1
    # A file lists the decisions that have no rule.
    detail = _coverage(api, "src/recargos.py")
    assert detail["uncovered"] and detail["uncovered"][0]["start_line"] == 2

    _verify(api)
    files = {child["name"]: child for child in _coverage(api, "src")["children"]}
    assert files["pagos.py"]["verified"] >= 1 and files["pagos.py"]["unverified"] == 0


def test_a_document_that_disagrees_with_the_code(api: Api) -> None:
    """Phase 8.2: the manual says 150; the commission rule decides with 100."""
    from tests.integration.test_ingestion import commit_all, write

    write(api.repo, "docs/manual.md",
          "# Manual\n\nLas ventas de mas de 150 pagan comision.\n\n"
          "La comision por venta es del 2 %.\n")  # fmt: skip
    commit_all(api.repo, "manual")
    api.sync()
    found = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/doc-conflicts").json()
    [conflict] = found["conflicts"]
    assert conflict["document"] == "docs/manual.md" and conflict["line"] == 3
    assert conflict["says"] == ["150"] and "100" in conflict["code"]
    assert conflict["title"] == "Comision por venta"


def test_a_process_from_an_entry_point_to_its_rules(api: Api) -> None:
    """Phase 8.3: an action handler calls the commission; the process lists its rule."""
    from tests.integration.test_ingestion import commit_all, write

    write(api.repo, "src/acciones.py",
          "from src.pagos import comision\n\n\n"
          "def action_cobrar(total):\n"
          "    neto = total - comision(total)\n"
          "    return neto\n")  # fmt: skip
    commit_all(api.repo, "accion de cobro")
    api.sync()
    found = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/processes").json()
    [process] = [p for p in found["processes"] if p["entry"] == "action_cobrar"]
    assert process["name"] == "cobrar" and process["rules"] == 1
    [step] = process["steps"]
    assert step["function"] == "comision" and step["depth"] == 1
    assert step["rules"][0]["title"] == "Comision por venta"


def test_changes_say_which_processes_a_rule_change_touches(api: Api) -> None:
    """Phase 8.4: the commission changes; the «cobrar» process goes through it."""
    from tests.integration.test_ingestion import commit_all, write
    from tests.integration.test_rule_watch import COMMISSION

    write(api.repo, "src/acciones.py",
          "from src.pagos import comision\n\n\n"
          "def action_cobrar(total):\n"
          "    return total - comision(total)\n")  # fmt: skip
    commit_all(api.repo, "accion de cobro")
    api.sync()
    write(api.repo, "src/pagos.py", COMMISSION.replace("total > 100", "total > 300"))
    found = api.runtime.client.get(
        f"/api/v1/projects/{api.project_id}/changes", params={"base": "main"}
    ).json()
    [change] = found["rules"]
    assert change["processes"] == ["cobrar"] and found["processes_affected"] == ["cobrar"]


def _by_domain(api: Api, domain: str | None = None, path: str = "") -> Any:
    params: dict[str, Any] = {"by_domain": "true", "path": path}
    if domain is not None:
        params["domain"] = domain
    response = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/coverage", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_coverage_by_domain_proposed_and_declared(api: Api) -> None:
    # Only a generic folder (src/): the proposal is that folder.
    proposed = _by_domain(api)
    assert [(c["name"], c["kind"], c["declared"]) for c in proposed["children"]] == [
        ("src", "domain", False)
    ]
    # A person names a domain for one file; the rest keep their proposal.
    (api.repo / ".scrinium").mkdir(exist_ok=True)
    (api.repo / ".scrinium" / "domains.yaml").write_text(
        'scrinium: 1\ndomains:\n  - name: "Pagos"\n    path: "src/pagos.py"\n', encoding="utf-8"
    )
    raw = _by_domain(api)
    declared = {c["name"]: c for c in raw["children"]}
    assert declared["Pagos"]["declared"] is True and declared["Pagos"]["files"] == 1
    assert declared["src"]["declared"] is False and declared["src"]["files"] == 1
    # Inside a domain, folders and files as usual, only its own.
    inside = _by_domain(api, "Pagos")
    assert [c["name"] for c in inside["children"]] == ["src"]
    assert [c["name"] for c in _by_domain(api, "Pagos", "src")["children"]] == ["pagos.py"]
    # A broken file is reported and the proposals stay.
    (api.repo / ".scrinium" / "domains.yaml").write_text("domains: [x]\n", encoding="utf-8")
    broken = _by_domain(api)
    assert broken["domain_errors"] and [c["name"] for c in broken["children"]] == ["src"]
