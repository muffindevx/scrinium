"""The provider flow end to end against devtools/mock_provider.py, a fake over real HTTPS.

Register, consent, extract with the provider, then isolated mode takes the consent away.
Nothing leaves this machine: the fake answers at mock-provider.localhost.
"""

import socket
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import Runtime
from tests.integration.test_analyses import Api
from tests.integration.test_analyses import repo as repo  # the fixture

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "devtools"))
import mock_provider

KEY = "sk-mock-0123456789abcdef"


def _free_port() -> int:
    with socket.socket(socket.AF_INET6) as probe:
        probe.bind(("::", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def mock_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    port = _free_port()
    server = mock_provider.serve(port, tmp_path)
    # Trust the fake's certificate only: the product's HTTPS checks stay as they are.
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "mock-provider.pem"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"https://{mock_provider.HOST}:{port}/v1"
    server.shutdown()


def test_rules_are_written_by_the_provider_and_isolation_takes_it_away(
    runtime: Runtime, repo: Path, mock_url: str
) -> None:
    api = Api(runtime, repo)
    # No local model answers: without the provider, extraction is refused up front.
    from scrinium.infrastructure.local_http import ModelStatus

    runtime.chat.state = ModelStatus.UNAVAILABLE
    runtime.llm._status_cache = None
    api.post(f"/api/v1/projects/{api.project_id}/extractions", {}, expected=422)

    provider = api.post("/api/v1/model-providers", {
        "name": "Mock", "kind": "openai_compatible", "base_url": mock_url, "api_key": KEY,
    }, 201)  # fmt: skip
    check = api.post(
        f"/api/v1/model-providers/{provider['id']}/check", {"model": mock_provider.CHAT_MODEL}, 200
    )
    assert check["status"] == "ready", check
    api.post(f"/api/v1/projects/{api.project_id}/ai/consents", {
        "provider_id": provider["id"], "model": mock_provider.CHAT_MODEL,
        "tasks": ["rule_map", "rule_statements"], "retention_acknowledged": True,
    }, 201)  # fmt: skip

    detail = api.read(api.post(f"/api/v1/projects/{api.project_id}/extractions", {})["id"])
    assert detail["status"] == "completed", detail
    assert len(detail["result"]["created"]) == 1
    assert runtime.chat.received == []  # the local model was never asked

    # Isolated mode: the consent goes, so extraction is local again, and refused.
    isolated = {"choice": "isolated"}
    response = runtime.client.put("/api/v1/ai-setup", json=isolated, headers=api.headers)
    assert response.status_code == 200, response.text
    state = runtime.client.get(f"/api/v1/projects/{api.project_id}/ai").json()
    assert state["consents"] == []
    api.post(f"/api/v1/projects/{api.project_id}/extractions", {}, expected=422)


def test_search_can_use_a_provider_and_isolation_sends_it_home(
    runtime: Runtime, repo: Path, mock_url: str
) -> None:
    api = Api(runtime, repo)
    put = runtime.client.put
    provider = api.post("/api/v1/model-providers", {
        "name": "Mock", "kind": "openai_compatible", "base_url": mock_url, "api_key": KEY,
    }, 201)  # fmt: skip
    body = {"kind": "provider", "provider_id": provider["id"], "model": mock_provider.EMBED_MODEL}
    # Every project's indexed text would go to the provider: refused until that is accepted.
    refused = put("/api/v1/ai-setup/search", json=body, headers=api.headers)
    assert refused.status_code == 422
    assert refused.json()["error"] == "search_provider_not_acknowledged"
    chosen = put(
        "/api/v1/ai-setup/search", json=body | {"sends_all_text": True}, headers=api.headers
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["search"]["kind"] == "provider"
    indexer = runtime.runner.indexer
    assert indexer.provider.model_id.startswith(f"remote:{mock_provider.HOST}/")
    indexer.embed_pending()
    found = runtime.client.get(
        f"/api/v1/projects/{api.project_id}/search", params={"q": "total de la factura"}
    ).json()
    assert found["semantic"] == "ready", found

    # What isolation would do is said first; then search is the bundled model again.
    preview = runtime.client.get("/api/v1/ai-setup/isolation").json()
    assert preview["search_model_reset"] is True and preview["answer_model_lost"] is False
    on = put("/api/v1/ai-setup/isolation", json={"on": True}, headers=api.headers).json()
    assert on["isolated"] is True and on["search"]["kind"] == "bundled"
    assert indexer.provider is indexer.bundled
    again = put(
        "/api/v1/ai-setup/search", json=body | {"sends_all_text": True}, headers=api.headers
    )
    assert again.status_code == 403 and again.json()["error"] == "isolated_mode"
    # A local server for answers stays allowed while isolated.
    local = {"choice": "local_server",
             "local_server": {"url": "http://127.0.0.1:11434", "model": "any-coder"}}  # fmt: skip
    kept = put("/api/v1/ai-setup", json=local, headers=api.headers).json()
    assert kept["isolated"] is True and kept["choice"] == "local_server"
    off = put("/api/v1/ai-setup/isolation", json={"on": False}, headers=api.headers).json()
    assert off["isolated"] is False and off["choice"] == "local_server"
