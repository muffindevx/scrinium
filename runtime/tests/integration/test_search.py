"""Hybrid search over indexed sources, with fake embeddings and real LanceDB."""

import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from scrinium.domain.enums import ItemLifecycle
from scrinium.infrastructure.local_http import ModelStatus
from scrinium.infrastructure.sqlite.models import ContentChunk, SourceItem
from tests.conftest import ORIGIN, Runtime
from tests.integration.test_ingestion import FAKE_KEY, commit_all, git, hostile_repo, write

__all__ = ["hostile_repo"]  # reused fixture


def _request(runtime: Runtime, csrf: str, method: str, url: str, **kwargs: Any) -> Any:
    return runtime.client.request(
        method, url, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}, **kwargs
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(
        root,
        "docs/facturacion.md",
        "# Facturación\n\nEl camión entrega facturas.\n\n"
        "## Impuestos\n\nEl IVA se calcula al final.\n",
    )
    write(
        root,
        "src/billing.py",
        "def calculate_invoice_total(lines):\n    return sum(l.amount for l in lines)\n",
    )
    write(root, "src/users.py", "def find_user_by_email(email):\n    return db.users.get(email)\n")
    write(root, "src/secret.py", 'API_KEY = "' + FAKE_KEY + '"\n')
    commit_all(root)
    return root


def _index(runtime: Runtime, csrf: str, root: Path, name: str = "App") -> tuple[str, str]:
    project = _request(runtime, csrf, "POST", "/api/v1/projects", json={"name": name}).json()["id"]
    created = _request(
        runtime, csrf, "POST", f"/api/v1/projects/{project}/sources",
        json={"kind": "git_repository", "path": str(root)},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    return project, created.json()["source"]["id"]


def _search(runtime: Runtime, project: str, query: str) -> dict[str, Any]:
    response = runtime.client.get(f"/api/v1/projects/{project}/search", params={"q": query})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_lexical_and_semantic_hits_cite_exact_lines(runtime: Runtime, repo: Path) -> None:
    csrf = runtime.login()
    project, _ = _index(runtime, csrf, repo)

    status = runtime.client.get(f"/api/v1/projects/{project}/index-status").json()
    assert status == {
        "chunks": 4,
        "embedded": 4,
        "provider": "ready",
        "model": "fake:bag-of-words",
    }

    body = _search(runtime, project, "calculate_invoice_total")
    assert body["semantic"] == "ready"
    top = body["results"][0]
    assert top["path"] == "src/billing.py"
    assert (top["start_line"], top["end_line"]) == (1, 2)
    assert top["commit"] == git(repo, "rev-parse", "HEAD")
    # It is a function name: the structural map finds it too.
    assert set(top["matched_by"]) == {"symbol", "lexical", "semantic"}

    # Without accents it finds accented text; and the heading travels in the citation.
    hit = _search(runtime, project, "camion")["results"][0]
    assert hit["path"] == "docs/facturacion.md"
    assert hit["heading"] == "Facturación"
    cited = runtime.client.get(
        f"/api/v1/source-revisions/{hit['revision_id']}/lines",
        params={"start": hit["start_line"], "end": hit["end_line"]},
    ).json()
    assert "\n".join(cited["lines"]) == hit["text"]

    # What was excluded as a secret does not exist for search.
    assert all(
        r["path"] != "src/secret.py" for r in _search(runtime, project, "API_KEY")["results"]
    )
    assert FAKE_KEY not in str(_search(runtime, project, "sk")["results"])


def test_only_current_revisions_are_searchable(runtime: Runtime, repo: Path) -> None:
    csrf = runtime.login()
    project, source = _index(runtime, csrf, repo)
    write(repo, "src/billing.py", "def compute_receipt(lines):\n    return 0\n")
    (repo / "src" / "users.py").unlink()
    commit_all(repo)
    _request(runtime, csrf, "POST", f"/api/v1/sources/{source}/sync")

    # Vector search always returns neighbours; what must not come back is the old text.
    for query in ("calculate_invoice_total", "find_user_by_email"):
        results = _search(runtime, project, query)["results"]
        assert all("lexical" not in r["matched_by"] for r in results)
        assert all(query not in r["text"] for r in results)
        assert all(r["path"] != "src/users.py" for r in results)
    assert _search(runtime, project, "compute_receipt")["results"][0]["path"] == "src/billing.py"
    with runtime.database.session() as session:
        # Old revisions keep their chunks for citations already made.
        assert session.scalar(select(func.count()).select_from(ContentChunk)) == 5


def test_projects_do_not_see_each_other(runtime: Runtime, repo: Path, tmp_path: Path) -> None:
    csrf = runtime.login()
    project_a, _ = _index(runtime, csrf, repo, "A")
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    write(other, "notes.md", "nada que ver\n")
    commit_all(other)
    project_b, _ = _index(runtime, csrf, other, "B")
    results_b = _search(runtime, project_b, "calculate_invoice_total")["results"]
    assert {r["path"] for r in results_b} <= {"notes.md"}
    assert _search(runtime, project_a, "calculate_invoice_total")["results"]


def test_without_ollama_search_is_lexical_and_catches_up_later(
    runtime: Runtime, repo: Path
) -> None:
    runtime.embeddings.state = ModelStatus.UNAVAILABLE
    runtime.runner.indexer._status_cache = None
    csrf = runtime.login()
    project, _ = _index(runtime, csrf, repo)

    body = _search(runtime, project, "calculate_invoice_total")
    assert body["semantic"] == "unavailable"
    assert body["results"][0]["matched_by"] == ["symbol", "lexical"]
    status = runtime.client.get(f"/api/v1/projects/{project}/index-status").json()
    assert (status["chunks"], status["embedded"]) == (4, 0)

    runtime.embeddings.state = ModelStatus.READY
    runtime.runner.indexer._status_cache = None
    runtime.runner.submit_embeddings()
    status = runtime.client.get(f"/api/v1/projects/{project}/index-status").json()
    assert (status["chunks"], status["embedded"]) == (4, 4)


def test_deleting_a_source_removes_chunks_fts_and_vectors(runtime: Runtime, repo: Path) -> None:
    csrf = runtime.login()
    project, source = _index(runtime, csrf, repo)
    assert _request(runtime, csrf, "DELETE", f"/api/v1/sources/{source}").status_code == 204
    assert _search(runtime, project, "calculate_invoice_total")["results"] == []
    with runtime.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ContentChunk)) == 0
        fts_rows = session.execute(text("SELECT count(*) FROM content_chunks_fts")).scalar()
        assert fts_rows == 0
    hits = runtime.runner.indexer.vector_index.search(
        "fake:bag-of-words", [1.0] * 64, uuid.UUID(project), 10
    )
    assert hits == []


def test_rebuild_regenerates_everything(runtime: Runtime, repo: Path) -> None:
    csrf = runtime.login()
    project, _ = _index(runtime, csrf, repo)
    with runtime.database.session() as session:
        before = set(session.scalars(select(ContentChunk.id)))
    calls = runtime.embeddings.calls

    assert _request(runtime, csrf, "POST", "/api/v1/index/rebuild").status_code == 202

    with runtime.database.session() as session:
        after = set(session.scalars(select(ContentChunk.id)))
    assert len(after) == 4 and after.isdisjoint(before)
    assert runtime.embeddings.calls > calls
    assert (
        _search(runtime, project, "calculate_invoice_total")["results"][0]["path"]
        == "src/billing.py"
    )


@pytest.mark.parametrize(
    "query",
    ['"', "chunk_id:*", "NEAR(", "a OR", "{path}", "*", "ñ" * 400, "\x00", "'; DROP TABLE x;--"],
)
def test_hostile_queries_never_fail(runtime: Runtime, repo: Path, query: str) -> None:
    csrf = runtime.login()
    project, _ = _index(runtime, csrf, repo)
    response = runtime.client.get(f"/api/v1/projects/{project}/search", params={"q": query})
    assert response.status_code == 200


def test_chunk_text_cannot_be_edited(runtime: Runtime, repo: Path) -> None:
    csrf = runtime.login()
    _index(runtime, csrf, repo)
    with pytest.raises(IntegrityError, match="solo anadir"), runtime.database.session() as session:
        session.execute(update(ContentChunk).values(text_content="otro"))


def test_search_needs_a_valid_query_and_project(runtime: Runtime, repo: Path) -> None:
    csrf = runtime.login()
    project, _ = _index(runtime, csrf, repo)
    assert (
        runtime.client.get(f"/api/v1/projects/{project}/search", params={"q": ""}).status_code
        == 422
    )
    too_long = {"q": "x" * 501}
    assert (
        runtime.client.get(f"/api/v1/projects/{project}/search", params=too_long).status_code == 422
    )
    missing = runtime.client.get(f"/api/v1/projects/{uuid.uuid4()}/search", params={"q": "x"})
    assert missing.status_code == 404


def test_inactive_items_disappear_from_search(runtime: Runtime, repo: Path) -> None:
    csrf = runtime.login()
    project, _ = _index(runtime, csrf, repo)
    with runtime.database.session() as session:
        session.execute(update(SourceItem).values(lifecycle_status=ItemLifecycle.DELETED))
    assert _search(runtime, project, "calculate_invoice_total")["results"] == []


@pytest.mark.skipif(
    os.environ.get("SCRINIUM_TEST_OLLAMA") != "1",
    reason="needs SCRINIUM_TEST_OLLAMA=1 and the model",
)
def test_real_semantic_search_with_ollama(tmp_path: Path) -> None:
    """Minimal evaluation with the real model: questions without the text's exact words."""
    from scrinium.core.config import default_embedding_model, default_ollama_url
    from scrinium.infrastructure.embeddings.ollama import OllamaEmbeddings

    provider = OllamaEmbeddings(default_ollama_url(), default_embedding_model())
    assert provider.status() is ModelStatus.READY
    corpus = {
        "billing": "def calculate_invoice_total(lines):\n    return sum(l.amount for l in lines)",
        "auth": (
            "def verify_password(user, candidate):\n    return bcrypt.checkpw(candidate, user.hash)"
        ),
        "deploy": (
            "# Despliegue\nPara publicar una versión, crea una etiqueta y ejecuta el pipeline."
        ),
    }
    vectors = dict(zip(corpus, provider.embed_documents(list(corpus.values())), strict=True))

    def best(question: str) -> str:
        query = provider.embed_query(question)
        return max(
            vectors, key=lambda key: sum(a * b for a, b in zip(query, vectors[key], strict=True))
        )

    assert best("¿Dónde se suman los importes de una factura?") == "billing"
    assert best("how are user credentials checked at login?") == "auth"
    assert best("¿cómo saco una release nueva?") == "deploy"
