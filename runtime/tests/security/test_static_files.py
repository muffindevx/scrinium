"""The UI is served without a session, so it must not allow leaving its folder."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scrinium.core.config import RuntimeConfig
from scrinium.core.session import LocalSessionManager
from scrinium.main import create_app, open_runtime
from tests.conftest import ORIGIN, TEST_PORT, memory_secret_store


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    web_dist = tmp_path / "web_dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text("<title>Scrinium</title>")
    secret = tmp_path / "secret.txt"
    secret.write_text("contenido-privado")
    (web_dist / "link.txt").symlink_to(secret)
    config = RuntimeConfig(port=TEST_PORT, data_dir=tmp_path / "data", web_dist=web_dist)
    database, environment = open_runtime(config, memory_secret_store())
    app = create_app(config, LocalSessionManager(), database, environment)
    app.state.ingestion.shutdown()
    app.state.questions.shutdown()
    app.state.analyses.shutdown()
    yield TestClient(app, base_url=ORIGIN)
    database.dispose()


def test_ui_is_served_with_security_headers(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Scrinium" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize(
    "path", ["/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/link.txt"]
)
def test_files_outside_the_ui_folder_are_not_served(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404
    assert "contenido-privado" not in response.text


def test_api_paths_never_fall_through_to_static_files(client: TestClient) -> None:
    assert client.get("/api/v1/does-not-exist").status_code == 401


def test_static_files_reject_writes(client: TestClient) -> None:
    assert client.post("/", headers={"Origin": ORIGIN}).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/reglas",
        "/reglas/1234",
        "/versiones",
        "/impacto/abc",
        "/ajustes",
        "/fuentes",
        "/actividad",
        "/integraciones",
    ],
)
def test_interface_routes_load_the_ui(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200 and "Scrinium" in response.text


@pytest.mark.parametrize(
    "path",
    [
        "/otra",
        "/rules",
        "/mapa",
        "/preguntar",
        "/reglas/../secret.txt",
        "/reglas/link.txt",
        "/reglas/x.js",
        "/link.txt",
    ],
)
def test_only_known_interface_routes_fall_back_to_the_ui(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 404
    assert "contenido-privado" not in response.text
