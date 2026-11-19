"""Structured API errors: every error response is ``{"error": {code, message, details}}``."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tennis_api.redaction import redact
from tennis_cv.errors import TennisCVError


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def not_found(entity: str, entity_id: str) -> ApiError:
    return ApiError(404, f"{entity}_not_found", f"{entity.capitalize()} '{entity_id}' not found")


def _body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    # Error bodies pass through ``redact``: no server path reaches the client (M1.10).
    @app.exception_handler(ApiError)
    async def _api_error(req: Request, exc: ApiError) -> JSONResponse:
        body = redact(_body(exc.code, exc.message, exc.details), req.app.state.settings)
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(TennisCVError)
    async def _engine_error(req: Request, exc: TennisCVError) -> JSONResponse:
        body = redact(_body(exc.code, exc.message, exc.details), req.app.state.settings)
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_body("request_invalid", "Request validation failed", {"errors": errors}),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=_body("http_error", str(exc.detail))
        )
