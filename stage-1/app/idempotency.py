import hashlib
import json
from typing import Optional, Union
from fastapi import Response
from app.errors import APIError

# The delivery constraints keep the whole tree runnable on Python 3.9, which has no
# `X | Y` annotations at runtime.
BodyData = Optional[Union[dict, list]]

MAX_KEY_LENGTH = 255

def compute_body_hash(body_data: BodyData) -> str:
    if body_data is None:
        serialized = ""
    else:
        serialized = json.dumps(body_data, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

def require_idempotency_key(key) -> str:
    if key is None or key == "":
        raise APIError(400, "missing_idempotency_key", "Idempotency-Key header is required")
    if not isinstance(key, str) or len(key) > MAX_KEY_LENGTH:
        raise APIError(422, "validation_failed",
                       f"Idempotency-Key must be 1 to {MAX_KEY_LENGTH} characters")
    return key

def check_idempotency(conn, scope: str, user_id: str, key: str, body_data: BodyData):
    """The stored response for a key this caller already used successfully.

    A replay is 200 whatever the original status was, and the body is the original
    byte-for-byte, so it stays identical as a JSON value even after the resource
    itself has changed or been cancelled.
    """
    full_scope = f"{scope}:{user_id}"
    row = conn.execute(
        "SELECT request_hash, response_body FROM idempotency_receipts WHERE scope = ? AND key = ?",
        (full_scope, key)
    ).fetchone()

    if not row:
        return None
    if row["request_hash"] != compute_body_hash(body_data):
        raise APIError(409, "idempotency_key_reuse",
                       "Idempotency-Key was already used with a different request body")
    return Response(content=row["response_body"], status_code=200, media_type="application/json")

def store_idempotency_receipt(conn, scope: str, user_id: str, key: str, body_data: BodyData, status_code: int, response_data: dict):
    full_scope = f"{scope}:{user_id}"
    conn.execute(
        "INSERT OR REPLACE INTO idempotency_receipts (scope, key, request_hash, status_code, response_body) VALUES (?, ?, ?, ?, ?)",
        (full_scope, key, compute_body_hash(body_data), status_code, json.dumps(response_data))
    )
