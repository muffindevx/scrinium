"""Local session and CSRF protection, including the 'protected by default' rule."""

from collections.abc import Iterator

import pytest
from starlette.routing import Route

from scrinium.api.routes import build_router
from tests.conftest import ORIGIN, Runtime

PUBLIC_GET_PATHS = {"/", "/health", "/ready", "/auth/launch"}


def _routes(runtime: Runtime) -> Iterator[tuple[str, str]]:
    # FastAPI wraps included routers: walk the router the app builds.
    for route in build_router(
        runtime.config,
        runtime.sessions,
        runtime.database,
        runtime.environment,
        runtime.runner,
        runtime.llm,
        runtime.app.state.questions,
    ).routes:
        if isinstance(route, Route):
            for method in route.methods or ():
                yield method, route.path


def test_every_api_route_requires_a_session(runtime: Runtime) -> None:
    checked = 0
    for method, path in _routes(runtime):
        if path in PUBLIC_GET_PATHS and method in {"GET", "HEAD"}:
            continue
        response = runtime.client.request(method, path, headers={"Origin": ORIGIN})
        assert response.status_code == 401, f"{method} {path}"
        checked += 1
    assert checked >= 4


def test_every_mutating_route_requires_csrf(runtime: Runtime) -> None:
    runtime.login()
    checked = 0
    for method, path in _routes(runtime):
        if method in {"GET", "HEAD"}:
            continue
        response = runtime.client.request(method, path, headers={"Origin": ORIGIN})
        assert response.status_code == 403, f"{method} {path}"
        assert response.json() == {"error": "csrf_invalid"}
        checked += 1
    assert checked >= 2


def test_unregistered_mutating_path_is_also_guarded(runtime: Runtime) -> None:
    response = runtime.client.post("/api/v1/does-not-exist", headers={"Origin": ORIGIN})
    assert response.status_code == 401


def test_post_without_origin_is_rejected_even_with_valid_csrf(runtime: Runtime) -> None:
    csrf = runtime.login()
    response = runtime.client.post("/api/v1/session/rotate", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 403
    assert response.json() == {"error": "origin_required"}


def test_wrong_csrf_is_rejected(runtime: Runtime) -> None:
    runtime.login()
    response = runtime.client.post(
        "/api/v1/session/rotate", headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"}
    )
    assert response.status_code == 403


def test_path_tricks_do_not_reach_the_api_without_session(runtime: Runtime) -> None:
    for path in (
        "//api/v1/session",
        "/API/v1/session",
        "/api/v1/../v1/session",
        "/%61pi/v1/session",
    ):
        response = runtime.client.get(path)
        assert response.status_code in {401, 404}, path
        assert "csrf_token" not in response.text


def test_launch_token_is_single_use(runtime: Runtime) -> None:
    token = runtime.sessions.issue_launch_token()
    first = runtime.client.get("/auth/launch", params={"token": token}, follow_redirects=False)
    assert first.status_code == 303
    second = runtime.client.get("/auth/launch", params={"token": token}, follow_redirects=False)
    assert second.status_code == 403


@pytest.mark.parametrize("token", ["", "wrong", "a" * 128])
def test_invalid_launch_token_gives_no_session(runtime: Runtime, token: str) -> None:
    runtime.sessions.issue_launch_token()
    response = runtime.client.get("/auth/launch", params={"token": token}, follow_redirects=False)
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_session_cookie_attributes(runtime: Runtime) -> None:
    token = runtime.sessions.issue_launch_token()
    response = runtime.client.get("/auth/launch", params={"token": token}, follow_redirects=False)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    assert "domain=" not in cookie


def test_rotation_invalidates_the_previous_session_and_csrf(runtime: Runtime) -> None:
    old_csrf = runtime.login()
    old_cookie = runtime.client.cookies["scrinium_session"]

    response = runtime.client.post(
        "/api/v1/session/rotate", headers={"Origin": ORIGIN, "X-CSRF-Token": old_csrf}
    )
    assert response.status_code == 200
    new_csrf = response.json()["csrf_token"]
    assert new_csrf != old_csrf
    assert runtime.client.cookies["scrinium_session"] != old_cookie

    runtime.client.cookies.set("scrinium_session", old_cookie)
    assert runtime.client.get("/api/v1/session").status_code == 401


def test_logout_ends_the_session_on_the_server(runtime: Runtime) -> None:
    csrf = runtime.login()
    cookie = runtime.client.cookies["scrinium_session"]
    response = runtime.client.post(
        "/api/v1/session/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
    )
    assert response.status_code == 204
    runtime.client.cookies.set("scrinium_session", cookie)
    assert runtime.client.get("/api/v1/session").status_code == 401


def test_api_documentation_is_not_exposed(runtime: Runtime) -> None:
    runtime.login()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert runtime.client.get(path).status_code == 404


def test_security_headers_are_present_on_every_response(runtime: Runtime) -> None:
    for response in (
        runtime.client.get("/health"),
        runtime.client.get("/health", headers={"Host": "evil.example"}),
        runtime.client.get("/api/v1/session"),
    ):
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-request-id"]


def test_api_responses_are_not_cached(runtime: Runtime) -> None:
    runtime.login()
    assert runtime.client.get("/api/v1/session").headers["cache-control"] == "no-store"
