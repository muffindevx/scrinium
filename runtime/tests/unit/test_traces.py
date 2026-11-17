"""Stack-trace frames of Python, JavaScript and the JVM (phase 7.3)."""

from scrinium.application.analysis.traces import parse_trace

PYTHON = """\
Traceback (most recent call last):
  File "/srv/app/.venv/lib/python3.12/site-packages/odoo/http.py", line 1873, in _serve_db
    return service_model.retrying(self._serve_ir_http, self.env)
  File "/srv/app/addons/sale/models/sale_order.py", line 1043, in action_confirm
    self._action_confirm()
ValueError: no se puede confirmar
"""

NODE = """\
TypeError: Cannot read properties of undefined (reading 'total')
    at calcularEnvio (/home/ana/tienda/src/envios.ts:14:22)
    at async Server.<anonymous> (file:///home/ana/tienda/src/api/pedidos.js:88:5)
    at Object.<anonymous> (/home/ana/tienda/node_modules/express/lib/router.js:12:3)
    at node:internal/process/task_queues:95:5
    at /home/ana/tienda/src/index.js:3:1
"""

BROWSER = "descuento@https://tienda.example/assets/descuentos.js:42:9\n"

JVM = """\
java.lang.IllegalStateException: pedido vacío
\tat com.tienda.pedidos.PedidoService.confirmar(PedidoService.java:57)
\tat com.tienda.api.PedidosController.post(PedidosController.kt:21)
\tat java.base/java.lang.Thread.run(Thread.java:1583)
"""


def test_python_frames() -> None:
    frames = parse_trace(PYTHON)
    assert [(f.path.rsplit("/", 1)[-1], f.line, f.function) for f in frames] == [
        ("http.py", 1873, "_serve_db"),
        ("sale_order.py", 1043, "action_confirm"),
    ]


def test_node_frames_skip_the_runtime_and_dependencies() -> None:
    frames = parse_trace(NODE)
    assert [(f.path, f.line, f.function) for f in frames] == [
        ("/home/ana/tienda/src/envios.ts", 14, "calcularEnvio"),
        ("/home/ana/tienda/src/api/pedidos.js", 88, "Server.<anonymous>"),
        ("/home/ana/tienda/src/index.js", 3, None),
    ]


def test_browser_and_jvm_frames() -> None:
    [browser] = parse_trace(BROWSER)
    assert (browser.path, browser.line, browser.function) == (
        "/assets/descuentos.js",
        42,
        "descuento",
    )
    frames = parse_trace(JVM)
    assert [(f.path, f.line, f.function) for f in frames[:2]] == [
        ("com/tienda/pedidos/PedidoService.java", 57, "confirmar"),
        ("com/tienda/api/PedidosController.kt", 21, "post"),
    ]
