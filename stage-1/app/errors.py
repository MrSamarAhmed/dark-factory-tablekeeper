from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from json import JSONDecodeError
from starlette.exceptions import HTTPException as StarletteHTTPException
import json

class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)

async def json_object_body(request: Request) -> dict:
    """The request body as a JSON object, or 400 `malformed_request`.

    Parsed here rather than declared with `Body(...)` so an unparseable body and a
    body of the wrong JSON shape both land on the status the contract names.
    """
    raw = await request.body()
    try:
        data = json.loads(raw) if raw else None
    except (ValueError, JSONDecodeError):
        raise APIError(400, "malformed_request", "Request body is not valid JSON")
    if not isinstance(data, dict):
        raise APIError(400, "malformed_request", "Request body must be a JSON object")
    return data

def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}}
    )

# Statuses FastAPI raises itself, mapped onto the shared error vocabulary.
HTTP_CODE_NAMES = {
    400: "malformed_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "not_found",
    409: "table_unavailable",
    422: "validation_failed",
}

def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError):
        return error_response(exc.status_code, exc.code, exc.message)

    @app.exception_handler(JSONDecodeError)
    async def json_decode_error_handler(request: Request, exc: JSONDecodeError):
        return error_response(400, "malformed_request", "Request body is not valid JSON")

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return error_response(400, "malformed_request", f"Request does not match its contract: {exc}")

    # An unmatched path or method never reaches a route handler, so without this the
    # framework's own `{"detail": ...}` body would answer in place of §5's envelope.
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        code = HTTP_CODE_NAMES.get(exc.status_code) or (
            "not_found" if exc.status_code < 500 else "internal"
        )
        response = error_response(exc.status_code, code, str(exc.detail))
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(Exception)
    async def catch_all_handler(request: Request, exc: Exception):
        if isinstance(exc, StarletteHTTPException):
            return await http_exception_handler(request, exc)
        return error_response(500, "internal", "Internal server error")
