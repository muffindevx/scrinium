"""Processes from routes registered with a call (Hono, Express, Fastify), with their prefix."""

import uuid

from scrinium.application.knowledge import processes as module
from scrinium.application.knowledge.processes import _routes_called, _Symbol, _trigger

ROUTES = """\
export function eventsRoutes(deps) {
  return new Hono().post(
    "/",
    rateLimit(deps.limiter),
    async (c) => {
      const { orgId } = c.get("apiKey");
      if (batch.length > 500) return c.json({}, 413);
      return c.json({ accepted: true });
    },
  ).get("/:id", async (c) => {
    return c.json(await load(c.req.param("id")));
  });
}
"""
APP = """\
export function buildApp(deps) {
  return new Hono().route("/v1/events", eventsRoutes(deps));
}
"""


def _symbol(
    name: str, path: str, text: str, start: int, rules: list[dict[str, object]] | None = None
) -> _Symbol:
    lines = text.count("\n")
    return _Symbol(uuid.uuid4(), name, name, path, start, start + lines, text,
                   rules or [], first_line=start)  # fmt: skip


def _rule(title: str, line: int) -> dict[str, object]:
    return {"id": title, "title": title, "statement": "", "verified": False,
            "start_line": line, "end_line": line}  # fmt: skip


def test_each_called_route_is_a_process_with_its_mounted_path() -> None:
    routes = _symbol("eventsRoutes", "apps/api/src/routes/events.ts", ROUTES, 1,
                     [_rule("Tamaño máximo del lote", 7)])  # fmt: skip
    limiter = _symbol("rateLimit", "apps/api/src/rate.ts", "function rateLimit(l) {}\n", 1,
                      [_rule("Límite de solicitudes", 1)])  # fmt: skip
    loader = _symbol("load", "apps/api/src/load.ts", "function load(id) {}\n", 1,
                     [_rule("Existe la ejecución", 1)])  # fmt: skip
    app = _symbol("buildApp", "apps/api/src/app.ts", APP, 1)
    symbols = {s.id: s for s in (routes, limiter, loader, app)}
    calls = {routes.id: [limiter.id, loader.id]}
    module._CALLEE_NAMES.update(
        {(routes.id, limiter.id): "rateLimit", (routes.id, loader.id): "load"}
    )
    found = {p["name"]: p for p in _routes_called(symbols, calls)}
    assert set(found) == {"POST /v1/events", "GET /v1/events/:id"}
    post = [r["title"] for step in found["POST /v1/events"]["steps"] for r in step["rules"]]
    # Its own rule first (inside the call), then the middleware it calls; not the GET's loader.
    assert post == ["Tamaño máximo del lote", "Límite de solicitudes"]
    get = [r["title"] for step in found["GET /v1/events/:id"]["steps"] for r in step["rules"]]
    assert get == ["Existe la ejecución"]


def test_context_reads_are_not_routes() -> None:
    text = 'function h(c) {\n  const user = c.get("session");\n  return c.get("apiKey");\n}\n'
    symbol = _symbol("h", "src/h.ts", text, 1, [_rule("Una regla", 2)])
    assert _routes_called({symbol.id: symbol}, {}) == []


def test_decorated_routes_name_their_method_and_path() -> None:
    text = '@app.post("/orders")\ndef create_order(request):\n    pass\n'
    assert _trigger(_symbol("create_order", "api.py", text, 1)) == {
        "kind": "http",
        "label": "POST /orders",
    }
    action = _symbol("action_confirm", "sale.py", "def action_confirm(self):\n", 1)
    assert _trigger(action)["kind"] == "action"
