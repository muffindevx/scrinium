"""Error responses with the same shape as the middleware's: `{"error": code}`."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


async def _http_error(_request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, HTTPException):
        raise error
    return JSONResponse(
        {"error": error.detail}, status_code=error.status_code, headers=error.headers
    )


async def _validation_error(_request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise error
    # Location and type only. FastAPI would also return `input` and `ctx`, echoing the request.
    fields = [{"loc": list(item["loc"]), "type": item["type"]} for item in error.errors()]
    return JSONResponse({"error": "invalid_request", "fields": fields}, status_code=422)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
