from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import re
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db import init_db, get_connection, transaction
from app.errors import register_exception_handlers, APIError
from app.seed import seed_database, load_fixture
from app.auth import hash_password, verify_password, generate_token_for_user
from app.exportimport import export_state, import_state

from app.restaurants import router as restaurants_router
from app.availability import router as availability_router
from app.reservations import router as reservations_router
from app.moves import router as moves_router


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


async def json_object_body(request: Request) -> dict:
    """
    Parse the request body as a JSON object.

    Contract:
      - invalid JSON -> 400 malformed_request
      - JSON null/array/string/number/bool -> 400 malformed_request
      - valid JSON object -> dict
    """
    raw = await request.body()

    if not raw:
        raise APIError(
            400,
            "malformed_request",
            "Request body must be a JSON object",
        )

    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        raise APIError(
            400,
            "malformed_request",
            "Request body is not valid JSON",
        )

    if not isinstance(data, dict):
        raise APIError(
            400,
            "malformed_request",
            "Request body must be a JSON object",
        )

    return data


def require_string(body: dict, field: str) -> str:
    """
    Require a field to be a JSON string.

    A missing field violates the endpoint contract (422); a field of the
    wrong JSON type is a malformed request (400).
    """
    value = body.get(field)

    if value is None:
        raise APIError(
            422,
            "validation_failed",
            f"{field} is required",
        )

    if not isinstance(value, str):
        raise APIError(
            400,
            "malformed_request",
            f"{field} must be a string",
        )

    return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # Seed a fresh database.
    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM restaurants"
        ).fetchone()["c"]

        if count == 0:
            seed_database(conn)

    yield


app = FastAPI(
    title="Tablekeeper",
    lifespan=lifespan,
)

register_exception_handlers(app)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(restaurants_router)
app.include_router(availability_router)
app.include_router(reservations_router)
app.include_router(moves_router)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Test reset
# ---------------------------------------------------------------------------

@app.post("/_test/reset", status_code=204)
async def test_reset(request: Request):
    """
    Reset the database.

    No body:
        restore the built-in seed.

    JSON object:
        load the supplied fixture.

    Invalid/non-object JSON is handled as malformed_request.
    Fixture-specific validation is handled by seed.load_fixture().
    """
    raw = await request.body()

    if not raw:
        with transaction() as conn:
            seed_database(conn)
        return None

    try:
        body = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        raise APIError(
            400,
            "malformed_request",
            "Request body is not valid JSON",
        )

    if not isinstance(body, dict):
        raise APIError(
            400,
            "malformed_request",
            "Request body must be a JSON object",
        )

    with transaction() as conn:
        load_fixture(conn, body)

    return None


# ---------------------------------------------------------------------------
# Export / import
# ---------------------------------------------------------------------------

@app.get("/_test/export")
def test_export():
    return export_state()


@app.post("/_test/import", status_code=204)
async def test_import(request: Request):
    body = await json_object_body(request)

    import_state(body)

    return None


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@app.post("/auth/signup", status_code=201)
async def auth_signup(request: Request):
    body = await json_object_body(request)

    email = require_string(body, "email")
    password = require_string(body, "password")
    display_name = require_string(body, "display_name")

    email = email.strip()
    display_name = display_name.strip()

    # Required by the Tablekeeper contract.
    if not email:
        raise APIError(
            422,
            "validation_failed",
            "email is required",
        )

    if not EMAIL_RE.fullmatch(email):
        raise APIError(
            422,
            "validation_failed",
            "email must be a valid email address",
        )

    if len(password) < 8:
        raise APIError(
            422,
            "validation_failed",
            "password must be at least 8 characters",
        )

    if not display_name:
        raise APIError(
            422,
            "validation_failed",
            "display_name is required",
        )

    clean_email = email.lower()

    with transaction() as conn:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE email = ?",
            (clean_email,),
        ).fetchone()

        if existing:
            raise APIError(
                409,
                "email_taken",
                f"Email already taken: {clean_email}",
            )

        user_id = f"usr-{uuid.uuid4().hex[:12]}"

        password_hash = hash_password(password)

        now_str = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """
            INSERT INTO users
                (user_id, email, password_hash, name, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                clean_email,
                password_hash,
                display_name,
                now_str,
            ),
        )

        token = generate_token_for_user(
            conn,
            user_id,
        )

        return {
            "user_id": user_id,
            "display_name": display_name,
            "token": token,
        }


@app.post("/auth/login")
async def auth_login(request: Request):
    body = await json_object_body(request)

    email = require_string(body, "email")
    password = require_string(body, "password")

    email = email.strip()

    if not email or not EMAIL_RE.fullmatch(email):
        raise APIError(
            422,
            "validation_failed",
            "email must be a valid email address",
        )

    if not password:
        raise APIError(
            422,
            "validation_failed",
            "password is required",
        )

    clean_email = email.lower()

    with transaction() as conn:
        user = conn.execute(
            """
            SELECT
                user_id,
                email,
                password_hash,
                name
            FROM users
            WHERE email = ?
            """,
            (clean_email,),
        ).fetchone()

        if not user:
            raise APIError(
                401,
                "unauthenticated",
                "Invalid email or password",
            )

        if not verify_password(
            password,
            user["password_hash"],
        ):
            raise APIError(
                401,
                "unauthenticated",
                "Invalid email or password",
            )

        token = generate_token_for_user(
            conn,
            user["user_id"],
        )

        return {
            "user_id": user["user_id"],
            "display_name": user["name"],
            "token": token,
        }