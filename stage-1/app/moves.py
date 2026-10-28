from typing import Optional

from fastapi import APIRouter, Header, Request

from app.auth import get_current_user_id
from app.db import transaction
from app.errors import APIError, json_object_body
from app.idempotency import (
    check_idempotency,
    require_idempotency_key,
    store_idempotency_receipt,
)
from app.reservations import (
    MOVES_SCOPE,
    enforce_cutoff,
    fetch_owned,
    json_response,
    optional_party_size,
    optional_string,
    place,
    require_string,
    reservation_payload,
)
from app.timeutil import from_utc_iso, local_label, utc_iso, zone_for

router = APIRouter(tags=["moves"])

MAX_MOVES = 8


def validate_moves(body: dict) -> list:
    """§11: 1..8 objects with distinct string references; anything else is 422."""
    if "moves" not in body:
        raise APIError(422, "validation_failed", "moves is required")
    moves = body["moves"]
    if not isinstance(moves, list) or not 1 <= len(moves) <= MAX_MOVES:
        raise APIError(422, "validation_failed",
                       f"moves must be a list of 1 to {MAX_MOVES} objects")
    seen = set()
    for move in moves:
        if not isinstance(move, dict):
            raise APIError(422, "validation_failed", "every move must be a JSON object")
        reference = move.get("reference")
        if not isinstance(reference, str) or not reference:
            raise APIError(422, "validation_failed", "every move needs a string reference")
        if reference in seen:
            raise APIError(422, "validation_failed", f"duplicate reference: {reference}")
        seen.add(reference)
    return moves


@router.post("/reservation-moves")
async def reservation_moves(
    request: Request,
    authorization: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    body = await json_object_body(request)
    user_id = get_current_user_id(authorization)
    key = require_idempotency_key(idempotency_key)

    with transaction() as conn:
        replayed = check_idempotency(conn, MOVES_SCOPE, user_id, key, body)
        if replayed is not None:
            return replayed

        moves = validate_moves(body)
        listed = [move["reference"] for move in moves]

        # §11 makes ownership and a single restaurant conditions on the whole batch, so
        # they are settled before any booking is changed -- a later unknown reference
        # must not be masked by an earlier item's own error.
        rows = []
        restaurant_id = None
        for move in moves:
            row = fetch_owned(conn, move["reference"], user_id)
            if restaurant_id is None:
                restaurant_id = row["restaurant_id"]
            elif row["restaurant_id"] != restaurant_id:
                raise APIError(422, "validation_failed", "a batch must stay inside one restaurant")
            rows.append(row)

        # Everything that is not occupancy, in input order, so the first offending
        # booking decides the error; its cutoff precedes any change to it.
        planned = []
        for move, row in zip(moves, rows):
            if row["status"] == "cancelled":
                raise APIError(409, "reservation_cancelled",
                               f"Reservation {move['reference']} is cancelled")
            enforce_cutoff(row)

            table_id = optional_string(move, "table_id", row["table_id"])
            party_size = optional_party_size(move, row["party_size"])
            if "starts_at_local" in move:
                starts_at_local = require_string(move, "starts_at_local")
            else:
                starts_at_local = local_label(from_utc_iso(row["starts_at"]),
                                              zone_for(row["timezone"]))

            restaurant, starts_at, ends_at = place(
                conn, restaurant_id, table_id, party_size, starts_at_local, exclude=listed)
            planned.append({
                "reference": move["reference"], "table_id": table_id,
                "party_size": party_size, "starts_at": starts_at, "ends_at": ends_at,
                "duration_minutes": int(restaurant["reservation_duration_minutes"]),
            })

        # The listed bookings are invisible to each other in the table, so their
        # resulting intervals are compared directly. An unchanged one keeps its own.
        for i, first in enumerate(planned):
            for second in planned[i + 1:]:
                if first["table_id"] == second["table_id"] \
                        and first["starts_at"] < second["ends_at"] \
                        and second["starts_at"] < first["ends_at"]:
                    raise APIError(409, "table_unavailable",
                                   f"Table {first['table_id']} is double-booked by this batch")

        for item in planned:
            conn.execute(
                """UPDATE reservations
                   SET table_id = ?, starts_at = ?, ends_at = ?, party_size = ?, duration_minutes = ?
                   WHERE reference = ?""",
                (item["table_id"], utc_iso(item["starts_at"]), utc_iso(item["ends_at"]),
                 item["party_size"], item["duration_minutes"], item["reference"]),
            )

        payload = {"reservations": [
            reservation_payload(fetch_owned(conn, item["reference"], user_id))
            for item in planned
        ]}
        store_idempotency_receipt(conn, MOVES_SCOPE, user_id, key, body, 201, payload)
        return json_response(payload, 201)
