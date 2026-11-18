"""Phase 9.3: integrity enforced by the database (R5), pending purges (R3), /ready (R8)."""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from scrinium.application.privacy import pending_purge
from scrinium.application.privacy.erasure import retry_pending_purges
from scrinium.infrastructure.sqlite.models import CodeEdge, CodeSymbol, SourceItem
from tests.conftest import ORIGIN
from tests.integration.test_analyses import Api
from tests.integration.test_rule_watch import api

__all__ = ["api"]


def test_a_file_cannot_point_to_another_files_revision(api: Api) -> None:
    with api.runtime.database.session() as session:
        first, second = session.scalars(select(SourceItem).limit(2)).all()
        other_revision = second.current_revision_id
        first_id = first.id
    database = api.runtime.database
    with pytest.raises(IntegrityError, match="otro archivo"), database.session() as session:
        session.execute(
            update(SourceItem)
            .where(SourceItem.id == first_id)
            .values(current_revision_id=other_revision)
        )


def test_a_call_edge_stays_in_its_project(api: Api) -> None:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": api.runtime.login()}
    other = api.runtime.client.post(
        "/api/v1/projects", json={"name": "Otro"}, headers=headers
    ).json()["id"]
    database = api.runtime.database
    database = api.runtime.database
    with pytest.raises(IntegrityError, match="otro proyecto"), database.session() as session:
        symbol = session.scalars(select(CodeSymbol)).first()
        assert symbol is not None
        edge = CodeEdge(
            workspace_id=symbol.workspace_id,
            project_id=uuid.UUID(other),
            from_symbol_id=symbol.id,
            to_symbol_id=symbol.id,
            callee="x",
            status="resolved",
        )
        session.add(edge)
        session.flush()


def test_a_failed_purge_is_retried_at_the_next_start(api: Api) -> None:
    data_dir = api.runtime.config.data_dir
    pending_purge.remember(data_dir, "source", uuid.uuid4())
    pending_purge.remember(data_dir, "all")
    assert len(json.loads((data_dir / pending_purge.PENDING_FILE).read_text())) == 2
    ready = api.runtime.client.get("/ready").json()
    assert ready["purges_pending"] == 2 and ready["vectors"] == "ok"
    remaining = retry_pending_purges(api.runtime.database, api.runtime.runner.indexer)
    assert remaining == 0 and not (data_dir / pending_purge.PENDING_FILE).exists()
    assert api.runtime.client.get("/ready").json()["purges_pending"] == 0


def test_a_purge_that_keeps_failing_stays_pending(api: Api) -> None:
    data_dir = api.runtime.config.data_dir
    pending_purge.remember(data_dir, "project", uuid.uuid4())

    def broken(_target: object) -> None:
        raise OSError("disco lleno")

    assert pending_purge.run_pending(data_dir, {"project": broken}) == 1
    assert pending_purge.pending(data_dir)[0]["kind"] == "project"


def test_ready_reports_vectors_and_models(api: Api) -> None:
    body = api.runtime.client.get("/ready").json()
    assert body["status"] == "ready" and body["database"] == "ok" and body["vectors"] == "ok"
    assert {"embeddings", "chat"} <= set(body)


def test_the_streamed_export_is_the_same_file_as_before(api: Api) -> None:
    """R1: written piece by piece, it opens with the same reader and holds the same rows."""
    from scrinium.application.privacy import export

    with api.runtime.database.scratch() as session:
        whole = export.collect(session)
        plain = b"".join(export.stream_export(session, None))
        sealed = b"".join(export.stream_export(session, "una clave larga de prueba"))
    streamed = json.loads(plain)
    assert streamed["tables"] == whole["tables"] and streamed["format"] == export.FORMAT
    opened = export.decrypt(sealed, "una clave larga de prueba")
    assert opened["tables"] == whole["tables"]
    with pytest.raises(export.ExportError, match="wrong_passphrase"):
        export.decrypt(sealed, "otra clave larga distinta")


def test_a_partial_clone_says_so_and_downloads_nothing(tmp_path: Path) -> None:
    """2.4: a clone with --filter=blob:none whose blobs are missing is reported as such."""
    import subprocess

    from scrinium.infrastructure.local_files.git_reader import (
        GitError,
        GitErrorCode,
        GitRepository,
    )

    def git(cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-c", "protocol.file.allow=always", *args],
            cwd=cwd, check=True, capture_output=True, text=True,
        ).stdout  # fmt: skip

    origin = tmp_path / "origen"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "a.py").write_text("x = 1\n")
    git(origin, "add", "-A")
    git(origin, "-c", "user.name=a", "-c", "user.email=a@b.invalid", "commit", "-qm", "c")
    git(origin, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "parcial"
    git(tmp_path, "clone", "-q", "--no-checkout", "--filter=blob:none",
        f"file://{origin}", str(clone))  # fmt: skip
    repository = GitRepository(clone)
    assert repository.is_partial()
    blob = git(clone, "rev-parse", "HEAD:a.py").strip()
    with pytest.raises(GitError) as error:
        list(repository.read_blobs([blob]))
    assert error.value.code is GitErrorCode.PARTIAL_CLONE


def test_the_api_answers_304_when_nothing_changed(api: Api) -> None:
    """9.4: weak ETags on JSON answers to GET; nothing reaches a disk cache (no-store)."""
    path = f"/api/v1/projects/{api.project_id}/rules"
    first = api.runtime.client.get(path)
    tag = first.headers["etag"]
    assert tag.startswith('W/"') and first.headers["cache-control"] == "no-store"
    again = api.runtime.client.get(path, headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.content == b""
    other = api.runtime.client.get(path, headers={"If-None-Match": 'W/"otra"'})
    assert other.status_code == 200 and other.json() == first.json()


def test_rules_come_in_pages_with_a_cursor(api: Api) -> None:
    from tests.integration.test_rule_watch import _reply

    api.runtime.chat.reply = _reply(
        [{"t": "Comision por venta", "s": "x", "c": "cal"},
         {"t": "Recargo de importe", "s": "y", "c": "cal"}]
    )  # fmt: skip
    path = f"/api/v1/projects/{api.project_id}/rules"
    every = api.runtime.client.get(path).json()["rules"]
    names, cursor = [], None
    while True:
        params = {"limit": "1", **({"cursor": cursor} if cursor else {})}
        page = api.runtime.client.get(path, params=params).json()
        names += [rule["name"] for rule in page["rules"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert sorted(names) == sorted(rule["name"] for rule in every)
    assert api.runtime.client.get(path, params={"limit": "1", "cursor": "%%"}).status_code == 422


def test_an_analysis_streams_its_progress(api: Api) -> None:
    """9.4: server-sent events until the analysis ends."""
    analyses = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/analyses").json()
    analysis_id = analyses["analyses"][0]["id"]
    with api.runtime.client.stream("GET", f"/api/v1/analyses/{analysis_id}/events") as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        text = "".join(stream.iter_text())
    assert "event: analysis" in text and text.rstrip().endswith("event: end\ndata: {}")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://usuario:clave@github.com/a/b",
        "https://token@github.com/a/b",
        "https://github.com/a/b?x=1",
        "https://203.0.113.5/a/b",
        "https://github.com",
        "file:///home/ana/repo",
        "ext::sh -c id",
    ],
)
def test_only_plain_https_urls_can_be_cloned(url: str) -> None:
    from scrinium.application.ingestion.clone import CloneError, check_url

    with pytest.raises(CloneError, match="invalid_url"):
        check_url(url)


def test_a_valid_url_is_stored_without_trailing_parts() -> None:
    from scrinium.application.ingestion.clone import check_url

    assert check_url(" https://GitHub.com/odoo/odoo/ ") == "https://github.com/odoo/odoo"


def test_the_hardened_git_refuses_local_transports(tmp_path: Path) -> None:
    """protocol.file.allow=never: even if a URL got through, git would not read a local path."""
    from scrinium.application.ingestion.clone import CloneError, _git

    with pytest.raises(CloneError, match="clone_failed"):
        _git(tmp_path, "clone", "--depth", "1", f"file://{tmp_path}", "x", error="clone_failed")


def test_a_source_says_which_commit_is_indexed_and_whether_head_moved(api: Api) -> None:
    from tests.integration.test_ingestion import commit_all, write

    path = f"/api/v1/projects/{api.project_id}/sources"
    [source] = api.runtime.client.get(path).json()["sources"]
    assert source["branch"] == "main" and source["head_moved"] is False
    assert source["indexed_commit"] == source["head_commit"]
    write(api.repo, "src/nuevo.py", "x = 1\n")
    commit_all(api.repo, "otro commit")
    [source] = api.runtime.client.get(path).json()["sources"]
    assert source["head_moved"] is True and source["indexed_commit"] != source["head_commit"]
