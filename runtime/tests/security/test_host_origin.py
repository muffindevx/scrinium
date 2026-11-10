"""A foreign website must not be able to talk to the local API."""

import pytest

from tests.conftest import ORIGIN, TEST_PORT, Runtime


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        f"evil.example:{TEST_PORT}",
        f"localhost:{TEST_PORT}",
        "127.0.0.1",
        "127.0.0.1:1",
        f"127.0.0.1.evil.example:{TEST_PORT}",
        f"[::1]:{TEST_PORT}",
    ],
)
def test_foreign_host_is_rejected_even_on_public_routes(runtime: Runtime, host: str) -> None:
    response = runtime.client.get("/health", headers={"Host": host})
    assert response.status_code == 403
    assert response.json() == {"error": "invalid_host"}


def test_allowed_host_reaches_public_route(runtime: Runtime) -> None:
    assert runtime.client.get("/health").json() == {"status": "ok", "service": "scrinium"}


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "null",
        f"http://localhost:{TEST_PORT}",
        "http://127.0.0.1:1",
        f"https://127.0.0.1:{TEST_PORT}",
        f"{ORIGIN}.evil.example",
    ],
)
def test_foreign_origin_is_rejected_with_a_valid_session(runtime: Runtime, origin: str) -> None:
    csrf = runtime.login()
    for response in (
        runtime.client.get("/api/v1/session", headers={"Origin": origin}),
        runtime.client.post(
            "/api/v1/session/rotate", headers={"Origin": origin, "X-CSRF-Token": csrf}
        ),
    ):
        assert response.status_code == 403
        assert response.json() == {"error": "invalid_origin"}


def test_no_cors_headers_ever(runtime: Runtime) -> None:
    runtime.login()
    responses = [
        runtime.client.get("/api/v1/session", headers={"Origin": ORIGIN}),
        runtime.client.get("/health", headers={"Origin": "https://evil.example"}),
        runtime.client.options(
            "/api/v1/session/rotate",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-csrf-token",
            },
        ),
    ]
    for response in responses:
        assert not [h for h in response.headers if h.lower().startswith("access-control-")]


def test_preflight_from_foreign_origin_fails(runtime: Runtime) -> None:
    response = runtime.client.options(
        "/api/v1/session/rotate",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 403
