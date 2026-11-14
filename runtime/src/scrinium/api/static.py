"""The built UI, with the browser routes (/reglas, /versiones...) served as
index.html so reloading a screen works.

Only a closed list of prefixes falls back to index.html; any other missing path still
returns 404, and real files are served by StaticFiles, which never leaves its folder.
"""

from typing import Final

from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# The same routes the UI defines in ui/src/App.tsx.
UI_ROUTES: Final = frozenset(
    {
        "preguntar",
        "reglas",
        "mapa",
        "arquitectura",
        "impacto",
        "versiones",
        "fuentes",
        "ia",
        "ajustes",
    }
)


def is_ui_route(path: str) -> bool:
    segments = path.strip("/").split("/")
    return segments[0] in UI_ROUTES and "." not in segments[-1]


class InterfaceFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or not is_ui_route(path):
                raise
            return await super().get_response("index.html", scope)
