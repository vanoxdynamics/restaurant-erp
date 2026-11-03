"""Domain exceptions raised by services and mapped to HTTP responses in app.main."""

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm.exc import StaleDataError

logger = logging.getLogger(__name__)


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(
        self,
        message: str = "",
        *,
        code: Optional[str] = None,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}
        self.headers = headers


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class InvalidTransition(Conflict):
    code = "invalid_transition"


class ValidationFailed(DomainError):
    status_code = 422
    code = "validation_failed"


class PayloadTooLarge(DomainError):
    status_code = 413
    code = "payload_too_large"


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"


class ApprovalRequired(Forbidden):
    code = "approval_required"


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


class TooManyAttempts(DomainError):
    status_code = 429
    code = "too_many_attempts"


def error_body(code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        headers = dict(exc.headers or {})
        if exc.status_code == 401:
            headers.setdefault("WWW-Authenticate", "Bearer")
        return JSONResponse(
            error_body(exc.code, exc.message, exc.details), status_code=exc.status_code, headers=headers
        )

    @app.exception_handler(StaleDataError)
    async def handle_stale(request: Request, exc: StaleDataError) -> JSONResponse:
        return JSONResponse(
            error_body("stale_version", "The record was modified by another request; reload and retry."),
            status_code=409,
        )

    @app.exception_handler(DBAPIError)
    async def handle_db_error(request: Request, exc: DBAPIError) -> JSONResponse:
        # Text the database cannot store (NUL bytes, invalid UTF-8) is the client's mistake, not a server fault.
        if "invalid byte sequence" in str(exc.orig):
            return JSONResponse(error_body("invalid_characters", "The request contains characters that cannot be stored."), status_code=422)
        raise exc

    @app.exception_handler(IntegrityError)
    async def handle_integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
        logger.warning("Integrity conflict on %s %s: constraint=%s", request.method, request.url.path, constraint)
        return JSONResponse(
            error_body("integrity_conflict", "The request conflicts with existing data.", {"constraint": constraint}),
            status_code=409,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception("Unhandled error on %s %s (request_id=%s)", request.method, request.url.path, request_id)
        return JSONResponse(
            error_body("internal_error", "An unexpected error occurred.", {"request_id": request_id}),
            status_code=500,
        )
