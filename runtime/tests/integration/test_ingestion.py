"""Ingestion of a Git repository and a folder prepared to attack Scrinium."""

import hashlib
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from scrinium.infrastructure.sqlite.models import (
    AuditEvent,
    SourceConnection,
    SourceItem,
    SourceRevision,
    SyncRun,
)
from tests.conftest import ORIGIN, Runtime

FAKE_KEY = "sk-" + "a1b2c3d4e5" * 4
PRIVATE_KEY = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\n"


def git(repo: Path, *args: str) -> str:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(repo.parent),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    # The test also uses git on the hostile repository: it overrides its configuration so
    # only Scrinium can trigger the marker.
    safe = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
    safe += ["-c", "filter.evil.clean=cat", "-c", "filter.evil.smudge=cat"]
    result = subprocess.run(
        ["git", "-C", str(repo), *safe, *args], capture_output=True, text=True, env=env, check=True
    )
    return result.stdout.strip()


def write(root: Path, relative: str, content: str | bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)


def commit_all(repo: Path, message: str = "c") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def tree_fingerprint(root: Path) -> dict[str, tuple[float, str]]:
    """Date and hash of every file, .git included: any write changes something."""
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            result[str(path.relative_to(root))] = (
                path.stat().st_mtime,
                hashlib.sha256(data).hexdigest(),
            )
    return result


@pytest.fixture
def hostile_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    files: dict[str, str | bytes] = {
        "README.md": "# Demo\nlinea 2\nlinea 3\n",
        "src/app.py": "def main():\n    password = 'changeme'\n    return 1\n",
        "src/settings.py": 'DATABASE = "x"\nAPI_KEY = "' + FAKE_KEY + '"\n',
        "src/db.py": 'URL = "postgres://admin:hunter2secret@db:5432/app"\n',
        "src/placeholder.py": 'password = "${DB_PASSWORD}"\ntoken = "<your-token-here>"\n',
        "docs/ñandú.md": "acentos\n",
        ".env": "TOKEN=abc\n",
        ".env.production": "TOKEN=abc\n",
        ".env.example": "TOKEN=\n",
        "deploy/server.pem": PRIVATE_KEY,
        "notes/key.txt": "hola\n" + PRIVATE_KEY,
        "node_modules/lib/index.js": "module.exports = 1\n",
        "package-lock.json": "{}\n",
        "dist/bundle.min.js": "x\n",
        "logo.png": b"\x89PNG\r\n\x1a\n\x00\x00",
        "data/blob.txt": b"texto\x00binario",
        "data/latin1.txt": "café".encode("latin-1"),
        "data/big.txt": "x" * (1024 * 1024 + 1),
        "local/notas.log": "no se indexa por patron\n",
        # Configuration that, if git obeyed it, would run commands.
        ".gitattributes": "*.md filter=evil diff=evil\n",
    }
    for relative, content in files.items():
        write(repo, relative, content)
    (repo / "passwd-link").symlink_to("/etc/passwd")
    commit_all(repo, "inicial")

    marker = repo.parent / "PWNED"
    with (repo / ".git" / "config").open("a") as config:
        config.write(
            f"[core]\n\tfsmonitor = touch {marker}\n\tpager = touch {marker}\n"
            f'[filter "evil"]\n\tsmudge = touch {marker}\n\tclean = touch {marker}\n'
            f'[diff "evil"]\n\ttextconv = touch {marker}\n'
            '[remote "origin"]\n\turl = https://example.invalid/repo.git\n'
            "\tpromisor = true\n"
        )
    for hook in ("post-checkout", "pre-commit", "post-index-change", "reference-transaction"):
        hook_path = repo / ".git" / "hooks" / hook
        hook_path.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook_path.chmod(0o755)
    # Submodule pointing to a commit that does not exist in this repository.
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor/sub")
    git(repo, "commit", "-q", "--no-verify", "-m", "submodulo")
    # Uncommitted change: Scrinium must read HEAD, not the folder.
    write(repo, "README.md", "SIN COMMIT\n")
    assert not marker.exists(), "the test itself triggered the hostile configuration"
    return repo


def _request(runtime: Runtime, csrf: str, method: str, url: str, **kwargs: Any) -> Any:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    return runtime.client.request(method, url, headers=headers, **kwargs)


def _project(runtime: Runtime, csrf: str) -> str:
    response = _request(runtime, csrf, "POST", "/api/v1/projects", json={"name": "Demo"})
    project_id: str = response.json()["id"]
    return project_id


def _body(path: Path, kind: str = "git_repository", **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "path": str(path), **extra}


def test_preview_explains_every_exclusion(runtime: Runtime, hostile_repo: Path) -> None:
    csrf = runtime.login()
    project = _project(runtime, csrf)
    before = tree_fingerprint(hostile_repo)

    response = _request(
        runtime, csrf, "POST", f"/api/v1/projects/{project}/sources/preview",
        json=_body(hostile_repo, exclude_patterns=["local/"]),
    )  # fmt: skip

    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["commit"] == git(hostile_repo, "rev-parse", "HEAD")
    included = {f["path"] for f in preview["included"]}
    assert included == {
        "README.md",
        "src/app.py",
        "src/placeholder.py",
        "docs/ñandú.md",
        ".env.example",
        ".gitattributes",
    }
    excluded = {e["path"]: (e["reason"], e["rule"], e["line"]) for e in preview["excluded"]}
    assert excluded["src/settings.py"] == ("possible_secret", "api_key_sk", 2)
    assert excluded["src/db.py"][:2] == ("possible_secret", "url_credentials")
    assert excluded["notes/key.txt"] == ("possible_secret", "private_key", 2)
    assert excluded[".env"][0] == "sensitive_file"
    assert excluded[".env.production"][0] == "sensitive_file"
    assert excluded["deploy/server.pem"][0] == "sensitive_file"
    assert excluded["node_modules/lib/index.js"][0] == "default_path"
    assert excluded["package-lock.json"][0] == "default_path"
    assert excluded["dist/bundle.min.js"][0] == "default_path"
    assert excluded["logo.png"][0] == "unsupported_type"
    assert excluded["data/blob.txt"][0] == "binary"
    assert excluded["data/latin1.txt"][0] == "not_utf8"
    assert excluded["data/big.txt"][0] == "too_large"
    assert excluded["local/notas.log"] == ("user_pattern", "local/", None)
    assert excluded["passwd-link"][0] == "symlink"
    assert excluded["vendor/sub"][0] == "submodule"
    # The secret is never returned, not even as context.
    assert FAKE_KEY not in response.text
    assert "hunter2secret" not in response.text
    assert "PRIVATE KEY" not in response.text

    assert not (hostile_repo.parent / "PWNED").exists()
    assert tree_fingerprint(hostile_repo) == before


def test_index_cite_resync_and_delete(runtime: Runtime, hostile_repo: Path) -> None:
    csrf = runtime.login()
    project = _project(runtime, csrf)
    before = tree_fingerprint(hostile_repo)

    created = _request(
        runtime, csrf, "POST", f"/api/v1/projects/{project}/sources", json=_body(hostile_repo)
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["source"]["id"]
    run = runtime.client.get(f"/api/v1/sync-runs/{created.json()['sync_run']['id']}").json()
    assert run["status"] == "succeeded", run
    assert run["items_created"] == 6
    assert run["items_excluded"] > 10
    assert not (hostile_repo.parent / "PWNED").exists()
    assert tree_fingerprint(hostile_repo) == before

    files = {
        f["path"]: f
        for f in runtime.client.get(f"/api/v1/sources/{source_id}/files").json()["files"]
    }
    readme = files["README.md"]
    cited = runtime.client.get(
        f"/api/v1/source-revisions/{readme['revision_id']}/lines", params={"start": 2, "end": 3}
    ).json()
    # What is in HEAD, not the folder's uncommitted change.
    assert cited["lines"] == ["linea 2", "linea 3"]
    assert cited["commit"] == git(hostile_repo, "rev-parse", "HEAD")
    assert cited["path"] == "README.md"

    # No changes: no new revision.
    resync = _request(runtime, csrf, "POST", f"/api/v1/sources/{source_id}/sync").json()
    resync = runtime.client.get(f"/api/v1/sync-runs/{resync['id']}").json()
    assert (resync["items_created"], resync["items_updated"], resync["items_deleted"]) == (0, 0, 0)
    assert resync["cursor_before"] == resync["cursor_after"]

    # A -> B -> A: the current revision is the original again.
    original_revision = readme["revision_id"]
    git(hostile_repo, "checkout", "-q", "--", "README.md")  # discards the uncommitted change
    write(hostile_repo, "README.md", "# Demo\nCAMBIO\n")
    (hostile_repo / "src" / "app.py").unlink()
    commit_all(hostile_repo, "cambio")
    second = _request(runtime, csrf, "POST", f"/api/v1/sources/{source_id}/sync").json()
    second = runtime.client.get(f"/api/v1/sync-runs/{second['id']}").json()
    assert (second["items_updated"], second["items_deleted"]) == (1, 1), second
    write(hostile_repo, "README.md", "# Demo\nlinea 2\nlinea 3\n")
    commit_all(hostile_repo, "vuelta")
    _request(runtime, csrf, "POST", f"/api/v1/sources/{source_id}/sync")
    files = {
        f["path"]: f
        for f in runtime.client.get(f"/api/v1/sources/{source_id}/files").json()["files"]
    }
    assert files["README.md"]["revision_id"] == original_revision
    assert "src/app.py" not in files
    with runtime.database.session() as session:
        assert session.scalar(select(func.count()).select_from(SourceRevision)) == 7

    deleted = _request(runtime, csrf, "DELETE", f"/api/v1/sources/{source_id}")
    assert deleted.status_code == 204
    with runtime.database.session() as session:
        for model in (SourceConnection, SourceItem, SourceRevision, SyncRun):
            assert session.scalar(select(func.count()).select_from(model)) == 0, model
        actions = session.scalars(select(AuditEvent.action)).all()
        assert {"source.create", "source.sync", "sync.finish", "source.delete"} <= set(actions)
    assert runtime.client.get(f"/api/v1/sources/{source_id}/files").status_code == 404


@pytest.mark.parametrize(
    ("make_path", "code"),
    [
        (lambda tmp: "relativa/repo", "path_not_absolute"),
        (lambda tmp: str(tmp / "no-existe"), "path_not_found"),
        (lambda tmp: str(tmp / "repo" / "README.md"), "not_a_directory"),
        (lambda tmp: str(tmp / "repo" / "src"), "not_repository_root"),
        (lambda tmp: str(tmp / "plain"), "not_a_git_repository"),
        (lambda tmp: str(tmp / "empty"), "empty_repository"),
        (lambda tmp: str(tmp), "contains_scrinium_data"),
        (lambda tmp: str(tmp / "data"), "contains_scrinium_data"),
    ],
)
def test_invalid_roots_are_rejected_without_details(
    runtime: Runtime, hostile_repo: Path, tmp_path: Path, make_path: Any, code: str
) -> None:
    (tmp_path / "plain").mkdir()
    (tmp_path / "empty").mkdir()
    git(tmp_path / "empty", "init", "-q")
    csrf = runtime.login()
    project = _project(runtime, csrf)
    response = _request(
        runtime, csrf, "POST", f"/api/v1/projects/{project}/sources/preview",
        json={"kind": "git_repository", "path": make_path(tmp_path)},
    )  # fmt: skip
    assert response.status_code == 400
    assert response.json() == {"error": code}


def test_sync_of_a_vanished_repository_fails_cleanly(runtime: Runtime, hostile_repo: Path) -> None:
    csrf = runtime.login()
    project = _project(runtime, csrf)
    source_id = _request(
        runtime, csrf, "POST", f"/api/v1/projects/{project}/sources", json=_body(hostile_repo)
    ).json()["source"]["id"]
    hostile_repo.rename(hostile_repo.with_name("moved"))
    run = _request(runtime, csrf, "POST", f"/api/v1/sources/{source_id}/sync").json()
    run = runtime.client.get(f"/api/v1/sync-runs/{run['id']}").json()
    assert run["status"] == "failed"
    assert run["error_summary"] == "path_not_found"
    listed = runtime.client.get(f"/api/v1/projects/{project}/sources").json()["sources"]
    assert listed[0]["status"] == "error"
    # What was indexed before is still there: a failure deletes nothing.
    assert listed[0]["active_files"] == 6


def test_folder_of_documents(runtime: Runtime, tmp_path: Path) -> None:
    folder = tmp_path / "docs"
    outside = tmp_path / "outside.md"
    outside.write_text("fuera de la raiz\n")
    write(folder, "guia.md", "# Guia\nuno\n")
    write(folder, "sub/notas.txt", "notas\n")
    write(folder, "codigo.py", "print(1)\n")
    write(folder, "secreto.md", "clave: " + FAKE_KEY + "\n")
    write(folder, ".ssh/id_rsa.md", "x\n")
    write(folder, "node_modules/x.md", "x\n")
    (folder / "enlace.md").symlink_to(outside)
    (folder / "dir-enlace").symlink_to(tmp_path)
    os.link(outside, folder / "duro.md")
    os.mkfifo(folder / "tuberia.md")
    deep = folder
    for level in range(40):
        deep = deep / f"n{level}"
    write(deep, "hondo.md", "x\n")

    csrf = runtime.login()
    project = _project(runtime, csrf)
    preview = _request(
        runtime, csrf, "POST", f"/api/v1/projects/{project}/sources/preview",
        json=_body(folder, kind="folder"),
    ).json()  # fmt: skip

    assert {f["path"] for f in preview["included"]} == {"guia.md", "sub/notas.txt"}
    reasons = {e["path"]: e["reason"] for e in preview["excluded"]}
    assert reasons["codigo.py"] == "unsupported_type"
    assert reasons["secreto.md"] == "possible_secret"
    assert reasons[".ssh/"] == "sensitive_file"
    assert reasons["node_modules/"] == "default_path"
    assert reasons["enlace.md"] == "symlink"
    assert reasons["dir-enlace"] == "symlink"
    assert reasons["duro.md"] == "hard_link"
    assert reasons["tuberia.md"] == "special_file"
    assert "too_deep" in reasons.values()
    assert "fuera de la raiz" not in str(preview)

    created = _request(
        runtime, csrf, "POST", f"/api/v1/projects/{project}/sources",
        json=_body(folder, kind="folder"),
    ).json()  # fmt: skip
    run = runtime.client.get(f"/api/v1/sync-runs/{created['sync_run']['id']}").json()
    assert run["status"] == "succeeded"
    assert run["cursor_after"] is None


def test_interrupted_syncs_are_marked_failed_on_restart(
    make_runtime: Any, hostile_repo: Path
) -> None:
    first = make_runtime()
    csrf = first.login()
    project = _project(first, csrf)
    source_id = _request(
        first, csrf, "POST", f"/api/v1/projects/{project}/sources", json=_body(hostile_repo)
    ).json()["source"]["id"]
    with first.database.session() as session:
        session.add(
            SyncRun(
                workspace_id=session.get_one(SourceConnection, uuid.UUID(source_id)).workspace_id,
                source_connection_id=uuid.UUID(source_id),
                trigger="manual",
                status="running",
            )
        )
    second = make_runtime()
    with second.database.session() as session:
        statuses = session.scalars(select(SyncRun.status)).all()
        assert "running" not in statuses
        assert (
            session.scalar(select(func.count()).where(SyncRun.error_summary == "interrupted")) == 1
        )


def test_one_sync_at_a_time(runtime: Runtime, hostile_repo: Path) -> None:
    csrf = runtime.login()
    project = _project(runtime, csrf)
    source_id = _request(
        runtime, csrf, "POST", f"/api/v1/projects/{project}/sources", json=_body(hostile_repo)
    ).json()["source"]["id"]
    with runtime.database.session() as session:
        connection = session.get_one(SourceConnection, uuid.UUID(source_id))
        session.add(
            SyncRun(
                workspace_id=connection.workspace_id,
                source_connection_id=connection.id,
                trigger="manual",
                status="running",
            )
        )
    assert _request(runtime, csrf, "POST", f"/api/v1/sources/{source_id}/sync").status_code == 409
    assert _request(runtime, csrf, "DELETE", f"/api/v1/sources/{source_id}").status_code == 409


def test_lines_endpoint_limits(runtime: Runtime, hostile_repo: Path) -> None:
    csrf = runtime.login()
    project = _project(runtime, csrf)
    source_id = _request(
        runtime, csrf, "POST", f"/api/v1/projects/{project}/sources", json=_body(hostile_repo)
    ).json()["source"]["id"]
    files = runtime.client.get(f"/api/v1/sources/{source_id}/files").json()["files"]
    revision = files[0]["revision_id"]
    url = f"/api/v1/source-revisions/{revision}/lines"
    assert runtime.client.get(url, params={"start": 0}).status_code == 422
    assert runtime.client.get(url, params={"start": 5, "end": 2}).status_code == 422
    assert runtime.client.get(url, params={"start": 999}).status_code == 422
    assert runtime.client.get(f"/api/v1/source-revisions/{uuid.uuid4()}/lines").status_code == 404


def test_unknown_project_is_not_found(runtime: Runtime, hostile_repo: Path) -> None:
    csrf = runtime.login()
    response = _request(
        runtime, csrf, "POST", f"/api/v1/projects/{uuid.uuid4()}/sources/preview",
        json=_body(hostile_repo),
    )  # fmt: skip
    assert response.status_code == 404


def test_hostile_config_is_live_for_plain_git(hostile_repo: Path) -> None:
    """Control: without the protections, git does run the repository's configuration."""
    env = {"PATH": os.environ["PATH"], "HOME": str(hostile_repo.parent)}
    subprocess.run(["git", "-C", str(hostile_repo), "status"], capture_output=True, env=env)
    assert (hostile_repo.parent / "PWNED").exists()


def test_generated_code_is_left_out_with_its_reason(runtime: Runtime, tmp_path: Path) -> None:
    repo = tmp_path / "generated"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo, "src/billing.py", "LIMIT = 3\n")
    write(repo, "proto/user.pb.go", "package proto\n")
    write(repo, "web/__generated__/types.ts", "export type A = 1\n")
    write(repo, "src/client.py", "# Code generated by tool. DO NOT EDIT.\nx = 1\n")
    commit_all(repo)
    csrf = runtime.login()
    project = _project(runtime, csrf)
    url = f"/api/v1/projects/{project}/sources"

    preview = _request(runtime, csrf, "POST", f"{url}/preview", json=_body(repo)).json()
    assert {f["path"] for f in preview["included"]} == {"src/billing.py"}
    excluded = {e["path"]: (e["reason"], e["rule"], e["line"]) for e in preview["excluded"]}
    assert excluded["proto/user.pb.go"] == ("generated", "*.pb.go", None)
    assert excluded["web/__generated__/types.ts"] == ("generated", "__generated__/", None)
    assert excluded["src/client.py"] == ("generated", "DO NOT EDIT", 1)

    wanted = _body(repo, include_generated=True)
    preview = _request(runtime, csrf, "POST", f"{url}/preview", json=wanted).json()
    assert len(preview["included"]) == 4

    created = _request(runtime, csrf, "POST", url, json=wanted)
    assert created.status_code == 201, created.text
    assert created.json()["source"]["include_generated"] is True
