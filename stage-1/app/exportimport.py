import re
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db import get_connection, transaction
from app.errors import APIError

TRACK = "tablekeeper"
FORMAT_VERSION = 1

# Forward dependency order: parents are inserted before children.
STATE_TABLES = (
    "restaurants",
    "tables",
    "opening_hours",
    "users",
    "auth_tokens",
    "reservations",
    "idempotency_receipts",
)

COLUMN_MAP = {
    "restaurants": (
        "id", "name", "city", "timezone",
        "slot_minutes", "reservation_duration_minutes",
        "cancellation_cutoff_minutes",
    ),
    "tables": ("id", "restaurant_id", "name", "max_seats", "position"),
    "opening_hours": ("restaurant_id", "weekday", "opens", "closes"),
    "users": ("user_id", "email", "password_hash", "name", "created_at"),
    "auth_tokens": ("token_hash", "user_id", "created_at"),
    "reservations": (
        "reference", "reservation_id", "restaurant_id", "user_id", "table_id",
        "starts_at", "ends_at", "party_size", "duration_minutes", "status",
        "created_at", "cancelled_at",
    ),
    "idempotency_receipts": (
        "scope", "key", "request_hash", "status_code", "response_body",
    ),
}

INTEGER_RANGES = {
    "restaurants": {
        "slot_minutes": (1, 2**63 - 1),
        "reservation_duration_minutes": (0, 2**63 - 1),
        "cancellation_cutoff_minutes": (0, 2**63 - 1),
    },
    "tables": {
        "max_seats": (1, 2**63 - 1),
        "position": (0, 2**63 - 1),
    },
    "opening_hours": {"weekday": (0, 6)},
    "reservations": {
        "party_size": (1, 2**63 - 1),
        "duration_minutes": (0, 2**63 - 1),
    },
    "idempotency_receipts": {"status_code": (200, 299)},
}
TIME_RE = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")


def export_state() -> dict:
    with get_connection() as conn:
        conn.execute("BEGIN;")
        state = {}
        for name in STATE_TABLES:
            columns = COLUMN_MAP[name]
            rows = conn.execute(
                f"SELECT {', '.join(columns)} FROM {name} ORDER BY rowid"
            ).fetchall()
            state[name] = [dict(row) for row in rows]
        return {
            "track": TRACK,
            "format_version": FORMAT_VERSION,
            "state": state,
        }


def _validate_document(data) -> None:
    """Structural validation of the whole document before anything is cleared."""
    if not isinstance(data, dict):
        raise APIError(422, "validation_failed", "Import document must be a JSON object")
    if data.get("track") != TRACK:
        raise APIError(422, "validation_failed", "Import document is for a different track")
    version = data.get("format_version")
    if isinstance(version, bool) or version != FORMAT_VERSION:
        raise APIError(422, "validation_failed", "Unsupported import format_version")
    state = data.get("state")
    if not isinstance(state, dict):
        raise APIError(422, "validation_failed", "Import document has no state object")
    for name in STATE_TABLES:
        rows = state.get(name)
        if not isinstance(rows, list):
            raise APIError(422, "validation_failed", f"state.{name} must be a list")
        for row in rows:
            if not isinstance(row, dict):
                raise APIError(422, "validation_failed",
                               f"each {name} entry must be an object")
            for column in COLUMN_MAP[name]:
                if column not in row:
                    raise APIError(422, "validation_failed",
                                   f"{name} entry is missing {column}")
            for column, (minimum, maximum) in INTEGER_RANGES.get(name, {}).items():
                value = row[column]
                if (isinstance(value, bool) or not isinstance(value, int)
                        or not minimum <= value <= maximum):
                    raise APIError(422, "validation_failed",
                                   f"{name}.{column} must be an integer between "
                                   f"{minimum} and {maximum}")
            if name == "restaurants":
                if not isinstance(row["timezone"], str):
                    raise APIError(422, "validation_failed",
                                   "restaurants.timezone must be a valid timezone")
                try:
                    ZoneInfo(row["timezone"])
                except (ZoneInfoNotFoundError, ValueError):
                    raise APIError(422, "validation_failed",
                                   "restaurants.timezone must be a valid timezone")
            if name == "opening_hours":
                for column in ("opens", "closes"):
                    value = row[column]
                    if not isinstance(value, str) or not TIME_RE.fullmatch(value):
                        raise APIError(422, "validation_failed",
                                       f"opening_hours.{column} must be exact HH:MM")
                if row["opens"] >= row["closes"]:
                    raise APIError(422, "validation_failed",
                                   "opening_hours.opens must be before closes")


def import_state(data) -> None:
    """Replace all state with the document, atomically.

    The document is validated in full before the destructive pass, and the
    write runs inside one transaction, so a rejected import leaves the current
    state untouched. A row that violates a database constraint rolls the whole
    transaction back rather than surfacing an SQLite error.
    """
    _validate_document(data)

    state = data["state"]

    with transaction() as conn:
        try:
            for name in reversed(STATE_TABLES):
                conn.execute(f"DELETE FROM {name}")

            for name in STATE_TABLES:
                columns = COLUMN_MAP[name]
                column_sql = ", ".join(columns)
                placeholders = ", ".join("?" for _ in columns)
                for row in state[name]:
                    conn.execute(
                        f"INSERT INTO {name} ({column_sql}) VALUES ({placeholders})",
                        [row[column] for column in columns],
                    )
        except (sqlite3.Error, OverflowError):
            raise APIError(422, "validation_failed",
                           "Import document contains invalid or conflicting state")
