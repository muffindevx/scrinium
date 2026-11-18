"""Tickets behind a change (phase 10.5): in `scrinium check`, in Cambios and in release notes.

From Git alone for `check` (commit messages and the branch name); Cambios also resolves the
keys against the connected tracker. No request leaves this machine.
"""

import json
import uuid
from pathlib import Path

import pytest

from scrinium.application.check.engine import CHANGED, REVIEW, check
from scrinium.application.check.git import Repository
from scrinium.application.check.report import to_json, to_markdown, to_sarif
from scrinium.application.check.rule_files import RULES_DIR, render
from scrinium.cli.rules import main as cli
from scrinium.domain.enums import ConnectionStatus, TicketLinkLevel, TicketProvider
from scrinium.infrastructure.sqlite.models import Project, Ticket, TicketConnection, TicketLink
from tests.integration.test_analyses import Api
from tests.integration.test_check import PAGOS, RULE_ID, _rule
from tests.integration.test_ingestion import commit_all, git, write
from tests.integration.test_rule_watch import COMMISSION, api

__all__ = ["api"]


@pytest.fixture(autouse=True)
def _no_ci_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The branch comes from Git here, not from the CI service running these tests."""
    for name in ("GITHUB_HEAD_REF", "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", "BITBUCKET_BRANCH",
                 "BUILDKITE_BRANCH", "CI_COMMIT_REF_NAME", "BRANCH_NAME"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "src/pagos.py", PAGOS)
    write(root, "src/otro.py", "def otro():\n    return 1\n")
    write(root, f"{RULES_DIR}/{RULE_ID}.md", render(_rule(PAGOS)))
    commit_all(root, "inicio")
    git(root, "checkout", "-q", "-b", "cambio")
    return root


def test_check_names_the_ticket_of_the_commit_that_changed_the_rule(repo: Path) -> None:
    write(repo, "src/otro.py", "def otro():\n    return 2\n")
    commit_all(repo, "PAY-9: otra cosa")  # another file: not this rule's ticket
    write(repo, "src/pagos.py", PAGOS.replace("> 100", "> 150"))
    commit_all(repo, "SHOP-221: sube el umbral de la comisión")
    result = check(Repository.at(repo), "main")
    [finding] = result.findings
    assert finding.level == CHANGED
    assert [(t.key, t.origin) for t in finding.tickets] == [("SHOP-221", "commit")]
    assert result.without_ticket == []
    report = json.loads(to_json(result))
    assert report["findings"][0]["tickets"][0]["key"] == "SHOP-221"
    assert report["branch"] == "cambio" and report["without_ticket"] == []
    sarif = json.loads(to_sarif(result))["runs"][0]["results"][0]
    assert sarif["properties"]["tickets"] == ["SHOP-221"] and "SHOP-221" in sarif["message"]["text"]
    assert "| SHOP-221 |" in to_markdown(result)


def test_a_rule_changed_without_a_ticket_is_flagged_and_can_fail(repo: Path) -> None:
    # An edited guard is only "review": it fails nothing, unless a ticket is required.
    write(repo, "src/pagos.py", PAGOS.replace("if total > 100:", "if total > 100 and total < 9:"))
    commit_all(repo, "ajustes")
    plain = check(Repository.at(repo), "main")
    assert [f.level for f in plain.findings] == [REVIEW] and not plain.failed
    assert [f.rule_id for f in plain.without_ticket] == [RULE_ID]
    assert "sin ticket" in to_markdown(plain)
    required = check(Repository.at(repo), "main", require_ticket=True)
    assert required.failed and "falla por eso" in to_markdown(required)
    assert cli(["check", "--base", "main", "--root", str(repo), "--require-ticket"]) == 1
    assert cli(["check", "--base", "main", "--root", str(repo)]) == 0


def test_the_branch_name_counts_and_covers_uncommitted_work(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "feature/shop-230-vip")
    write(repo, "src/pagos.py", PAGOS.replace("if total > 100:", "if total > 100 and total < 9:"))
    result = check(Repository.at(repo), "main", include_local=True, require_ticket=True)
    [finding] = result.findings
    assert [(t.key, t.origin, t.where) for t in finding.tickets] == [
        ("SHOP-230", "branch", "feature/shop-230-vip")
    ]
    assert not result.failed


def test_ci_names_the_branch_of_a_detached_clone(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(repo, "src/pagos.py", PAGOS.replace("if total > 100:", "if total > 100 and total < 9:"))
    commit_all(repo, "ajustes")
    git(repo, "checkout", "-q", "--detach")
    monkeypatch.setenv("GITHUB_HEAD_REF", "OPS-12-limites")
    result = check(Repository.at(repo), "main", require_ticket=True)
    assert result.branch == "OPS-12-limites" and not result.failed


def _connect(api: Api) -> None:
    """A Linear connection with two stored tickets: one renamed, one linked to a commit."""
    with api.runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(api.project_id))
        connection = TicketConnection(
            workspace_id=project.workspace_id, project_id=project.id,
            provider=TicketProvider.LINEAR, host="api.linear.app", display_name="Acme",
            status=ConnectionStatus.ACTIVE, auth="api_key", key_hint="abcd",
            scope=[{"id": "t", "key": "SHOP", "name": "Tienda"}], cursor={},
        )  # fmt: skip
        session.add(connection)
        session.flush()
        for external, key, title, previous in (
            ("a", "SHOP-221", "Niveles oro", ["ENG-12"]),
            ("b", "SHOP-300", "Comisión de campaña", []),
        ):
            session.add(Ticket(
                workspace_id=project.workspace_id, project_id=project.id,
                connection_id=connection.id, external_id=external, container_id="t", key=key,
                previous_keys=previous, title=title, status="Done", status_category="done",
                labels=[], url=f"https://linear.app/acme/issue/{key}",
            ))  # fmt: skip


def _link_commit(api: Api, key: str, sha: str) -> None:
    with api.runtime.database.session() as session:
        ticket = session.query(Ticket).filter_by(key=key).one()
        session.add(TicketLink(
            workspace_id=ticket.workspace_id, project_id=ticket.project_id, ticket_id=ticket.id,
            from_kind="commit", from_ref=sha, level=TicketLinkLevel.EXPLICIT,
            origin="linear:attachment",
        ))  # fmt: skip


def test_changes_resolve_old_keys_and_explicit_links(api: Api) -> None:
    _connect(api)
    git(api.repo, "checkout", "-q", "-b", "campana")
    write(api.repo, "src/pagos.py", COMMISSION.replace("total > 100", "total > 200"))
    sha = commit_all(api.repo, "ENG-12 sube el umbral").strip()  # an old key of SHOP-221
    _link_commit(api, "SHOP-300", sha)
    git(api.repo, "checkout", "-q", "main")
    path = f"/api/v1/projects/{api.project_id}/changes"
    found = api.runtime.client.get(path, params={"base": "main", "head": "campana"}).json()
    [rule] = found["rules"]
    assert [(t["key"], t["level"], t["ticket"]["title"]) for t in rule["tickets"]] == [
        ("SHOP-300", "explicit", "Comisión de campaña"),
        ("SHOP-221", "mentioned", "Niveles oro"),
    ]
    assert rule["without_ticket"] is False and found["without_ticket"] == 0
    assert found["branch"] == "campana"
    by_sha = api.runtime.client.get(path, params={"base": "main", "head": sha}).json()
    assert by_sha["branch"] is None  # a commit is not a branch
    notes = api.runtime.client.get(
        path, params={"base": "main", "head": "campana", "format": "markdown"}
    ).text
    assert "SHOP-300 «Comisión de campaña», SHOP-221 «Niveles oro»" in notes


def test_changes_flag_a_rule_changed_without_a_ticket(api: Api) -> None:
    write(api.repo, "src/pagos.py", COMMISSION.replace("total > 100", "total > 150"))
    path = f"/api/v1/projects/{api.project_id}/changes"
    found = api.runtime.client.get(path, params={"base": "main"}).json()
    [rule] = found["rules"]
    assert rule["tickets"] == [] and rule["without_ticket"] is True
    assert found["without_ticket"] == 1 and found["branch"] == "main"
    notes = api.runtime.client.get(path, params={"base": "main", "format": "markdown"}).text
    assert "sin ticket" in notes


def test_bugs_raise_a_rule_in_the_inbox_and_show_in_coverage(api: Api) -> None:
    from scrinium.infrastructure.sqlite.models import KnowledgeEntity

    base = f"/api/v1/projects/{api.project_id}"
    before = api.runtime.client.get(f"{base}/inbox").json()["items"]
    [item] = [i for i in before if i["title"] == "Comision por venta"]
    assert item["parts"]["bugs"] == 0
    _connect(api)
    sha = "c" * 40
    with api.runtime.database.session() as session:
        for ticket in session.query(Ticket).all():
            ticket.ticket_type = "bug"
        entity = session.get_one(KnowledgeEntity, uuid.UUID(item["entity_id"]))
        # Its commits name an old key of SHOP-221; Linear links SHOP-300 to another commit.
        entity.metadata_ = {**entity.metadata_, "tickets": ["ENG-12"], "commits": [sha]}
    _link_commit(api, "SHOP-300", sha)
    after = api.runtime.client.get(f"{base}/inbox").json()["items"]
    [raised] = [i for i in after if i["title"] == "Comision por venta"]
    assert raised["parts"]["bugs"] == 2 and raised["score"] > item["score"]
    coverage = api.runtime.client.get(f"{base}/coverage", params={"path": "src"}).json()
    files = {child["name"]: child for child in coverage["children"]}
    assert files["pagos.py"]["bugs"] == 2 and files["recargos.py"]["bugs"] == 0
    assert coverage["total"]["bugs"] == 2
    # A deleted ticket stops counting.
    with api.runtime.database.session() as session:
        session.query(Ticket).filter_by(key="SHOP-300").one().tombstone = True
    again = api.runtime.client.get(f"{base}/inbox").json()["items"]
    assert next(i for i in again if i["title"] == "Comision por venta")["parts"]["bugs"] == 1


def test_triage_from_a_bug_ticket(api: Api) -> None:
    _connect(api)
    with api.runtime.database.session() as session:
        bug = session.query(Ticket).filter_by(key="SHOP-221").one()
        bug.ticket_type, bug.title = "bug", "La comisión por venta sale mal"
        old = session.query(Ticket).filter_by(key="SHOP-300").one()
        old.ticket_type, old.title = "bug", "Comisión por venta duplicada"
        bug_id = str(bug.id)
    # One commit names the old key and touches the rule's lines; one names SHOP-2210, not it.
    write(api.repo, "src/pagos.py", COMMISSION.replace("total > 100", "total > 120"))
    first = commit_all(api.repo, "ENG-12: corrige la comisión").strip()
    write(api.repo, "src/recargos.py", "def otro():\n    return 3\n")
    commit_all(api.repo, "SHOP-2210 otra cosa")
    found = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/tickets/{bug_id}/triage")
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["ticket"]["key"] == "SHOP-221"
    assert [(c["sha"], c["level"], c["files"]) for c in body["commits"]] == [
        (first, "mentioned", ["src/pagos.py"])
    ]
    [rule] = body["rules"]
    assert rule["title"] == "Comision por venta" and rule["commits"] == [first]
    assert [t["key"] for t in body["similar"]] == ["SHOP-300"]
    bugs = api.runtime.client.get(
        f"/api/v1/projects/{api.project_id}/tickets", params={"bugs": "true"}
    ).json()["tickets"]
    assert {t["key"] for t in bugs} == {"SHOP-221", "SHOP-300"}
    missing = api.runtime.client.get(
        f"/api/v1/projects/{api.project_id}/tickets/{uuid.uuid4()}/triage"
    )
    assert missing.status_code == 404
