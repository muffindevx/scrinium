"""Control routes for the command line: `scrinium stop` and `scrinium attach`.

LocalGuardMiddleware protects them: the instance-registry key and no Origin.
"""

import logging
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any, Final

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse

from scrinium.core.log_hub import HUB

# Short: when Scrinium stops, the thread waiting here finishes right away.
_HEARTBEAT_SECONDS: Final = 2

logger = logging.getLogger("scrinium.control")


def build_control_router() -> APIRouter:
    router = APIRouter()

    @router.post("/control/shutdown", status_code=status.HTTP_202_ACCEPTED)
    def shutdown(request: Request) -> dict[str, Any]:
        """Like closing the window: the server shuts down in order, and llama-server with it."""
        stop: Callable[[], None] | None = getattr(request.app.state, "request_shutdown", None)
        if stop is None:
            return {"status": "unsupported"}
        logger.warning("Detencion pedida con scrinium stop")
        # After responding: otherwise the command line would see the connection cut.
        threading.Timer(0.2, stop).start()
        return {"status": "stopping"}

    @router.get("/control/logs")
    def logs(contenido: bool = False) -> StreamingResponse:
        subscription = HUB.subscribe(contenido)
        logger.info("Consola enganchada con scrinium attach (contenido=%s)", contenido)

        def stream() -> Iterator[str]:
            try:
                while True:
                    try:
                        line = subscription.lines.get(timeout=_HEARTBEAT_SECONDS)
                    except queue.Empty:
                        # Empty line: keeps the connection alive and notices when it is dropped.
                        yield "\n"
                        continue
                    if line is None:
                        return
                    yield line + "\n"
            finally:
                HUB.unsubscribe(subscription)

        return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")

    return router
