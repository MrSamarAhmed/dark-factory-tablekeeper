"""Availability and reservation lifecycle (§8) plus occupancy (§3 of the model
rules): half-open intervals, slot grid, opening hours, capacity and cutoffs.

Derived from the Stage 1 requirements quoted in `room.json` only.
"""

import re
from datetime import timedelta


def error_code(response):
    assert response.status_code >= 400, response.text
    return response.json()["error"]["code"]


def slot_local_starts(body):
    return [slot["starts_at_local"] for slot in body["slots"]]


def inside_cutoff(stage, restaurant_id="r_late", table_id="t_9"):
    """A start that is inside the 120-minute cancellation cutoff, and bookable."""
    today = stage.local_now().strftime("%Y-%m-%d")
    candidate = stage.on_grid_ahead(30)
    latest = f"{today}T22:00"  # 22:00 + 90 min still ends before 23:59
    start = min(candidate, latest)
    response = stage.book("k-cutoff-target", start, table_id=table_id,
                          party_size=2, restaurant_id=restaurant_id)
    assert response.status_code == 201, response.text
    return response.json()["reference"]


# ---------------------------------------------------------------------------
# GET /availability
# ---------------------------------------------------------------------------

def test_availability_requires_all_three_parameters(stage):
    for name in ("restaurant_id", "date", "party_size"):
        query = {"restaurant_id": "r_anker", "date": stage.thu, "party_size": 2}
        del query[name]
        response = stage.client.get("/availability", params=query)
        assert response.status_code == 422, name
        assert error_code(response) == "validation_failed"


def test_availability_lists_the_slot_grid_inside_opening_hours(stage):
    body = stage.availability(party_size=2).json()

    assert body["restaurant_id"] == "r_anker"
    assert body["date"] == stage.thu
    assert body["timezone"] == "Europe/Berlin"

    starts = slot_local_starts(body)
    # 18:00 to 21:30 in 30-minute steps: 21:30 + 90 min == 23:00 closes.
    assert starts[0] == f"{stage.thu}T18:00"
    assert starts[-1] == f"{stage.thu}T21:30"
    assert len(starts) == 8
    assert f"{stage.thu}T22:00" not in starts

    for slot in body["slots"]:
        assert re.fullmatch(rf"{stage.thu}T[0-9]{{2}}:[0-9]{{2}}", slot["starts_at_local"])
        assert slot["starts_at"].startswith(slot["starts_at_local"])


def test_availability_filters_tables_by_capacity_and_keeps_fixture_order(stage):
    assert stage.availability(party_size=2).json()["slots"][0]["available_table_ids"] == ["t_1", "t_2", "t_3"]
    assert stage.availability(party_size=3).json()["slots"][0]["available_table_ids"] == ["t_2", "t_3"]
    assert stage.availability(party_size=4).json()["slots"][0]["available_table_ids"] == ["t_2", "t_3"]


def test_availability_excludes_tables_with_overlapping_reservations(stage):
    stage.book("k-busy", f"{stage.thu}T19:00", table_id="t_2", party_size=4)

    slot = next(item for item in stage.availability(party_size=4).json()["slots"]
                if item["starts_at_local"] == f"{stage.thu}T19:00")
    assert slot["available_table_ids"] == ["t_3"]

    # Half-open interval: a 19:00-20:30 booking does not block a 20:30 start.
    later = next(item for item in stage.availability(party_size=4).json()["slots"]
                 if item["starts_at_local"] == f"{stage.thu}T20:30")
    assert "t_2" in later["available_table_ids"]


def test_availability_keeps_slots_with_no_free_table(stage):
    stage.book("k-one", f"{stage.thu}T18:00", table_id="t_2", party_size=4)
    stage.book("k-two", f"{stage.thu}T18:00", table_id="t_3", party_size=4)

    slot = next(item for item in stage.availability(party_size=4).json()["slots"]
                if item["starts_at_local"] == f"{stage.thu}T18:00")
    assert slot["available_table_ids"] == []


def test_availability_of_a_closed_day_is_empty(stage):
    body = stage.availability(day=_next(stage.thu, 2), party_size=2).json()
    assert body["slots"] == []


def _next(iso_date, days):
    from datetime import date

    return (date.fromisoformat(iso_date) + timedelta(days=days)).isoformat()


def test_availability_rejects_malformed_query_values(stage):
    for party in ("4.0", "1e9", "+4", "abc", "four"):
        response = stage.availability(party_size=party)
        assert response.status_code == 422, party
        assert error_code(response) == "validation_failed"

    for bad_date in ("2026-13-01", "not-a-date", ""):
        response = stage.availability(day=bad_date)
        assert response.status_code == 422, bad_date
        assert error_code(response) == "validation_failed"


def test_availability_of_unknown_restaurant_is_not_found(stage):
    response = stage.availability(restaurant_id="r-nope")
    assert response.status_code == 404
    assert error_code(response) == "not_found"


# ---------------------------------------------------------------------------
# POST /reservations
# ---------------------------------------------------------------------------

def test_create_returns_the_documented_reservation_shape(stage, slot_local):
    response = stage.book("k-shape", slot_local, table_id="t_2", party_size=4)
    assert response.status_code == 201

    body = response.json()
    assert set(body) == {"reservation_id", "reference", "restaurant_id", "table_id",
                         "party_size", "status", "starts_at_local", "starts_at",
                         "ends_at", "created_at"}
    assert re.fullmatch(r"[A-Z0-9]{6,12}", body["reference"])
    assert body["status"] == "confirmed"
    assert body["restaurant_id"] == "r_anker"
    assert body["table_id"] == "t_2"
    assert body["starts_at_local"] == slot_local
    assert body["starts_at"].startswith(slot_local)


def test_ends_at_is_absolute_duration_from_the_start(stage, slot_local):
    body = stage.book("k-duration", slot_local).json()

    from datetime import datetime

    start = datetime.fromisoformat(body["starts_at"])
    end = datetime.fromisoformat(body["ends_at"])
    assert end - start == timedelta(minutes=90)


def test_create_ignores_unknown_body_fields(stage, slot_local):
    response = stage.book("k-extra", slot_local, notes="window seat", channel=3)
    assert response.status_code == 201, response.text


def test_create_rejects_starts_off_the_slot_grid(stage):
    response = stage.book("k-grid", f"{stage.thu}T19:07")
    assert response.status_code == 422
    assert error_code(response) == "not_on_slot_grid"


def test_create_rejects_a_window_that_ends_after_closing(stage):
    response = stage.book("k-late", f"{stage.thu}T22:00")
    assert response.status_code == 422
    assert error_code(response) == "outside_opening_hours"


def test_create_rejects_a_closed_day(stage):
    response = stage.book("k-closed", f"{_next(stage.thu, 2)}T19:00")
    assert response.status_code == 422
    assert error_code(response) == "outside_opening_hours"


def test_create_rejects_party_above_table_capacity(stage):
    response = stage.book("k-cap", f"{stage.thu}T19:00", table_id="t_1", party_size=4)
    assert response.status_code == 422
    assert error_code(response) == "party_exceeds_capacity"


def test_create_rejects_invalid_party_size(stage, slot_local):
    for party in (0, -1, "4", True, 2.5):
        response = stage.book(f"k-party-{party}", slot_local, party_size=party)
        assert response.status_code == 422, party
        assert error_code(response) == "validation_failed"


def test_create_of_unknown_restaurant_or_table_is_not_found(stage, slot_local):
    unknown_restaurant = stage.book("k-r", slot_local, restaurant_id="r-nope")
    assert unknown_restaurant.status_code == 404
    assert error_code(unknown_restaurant) == "not_found"

    unknown_table = stage.book("k-t", slot_local, table_id="t-nope")
    assert unknown_table.status_code == 404
    assert error_code(unknown_table) == "not_found"

    # t_9 belongs to r_late, so asking for it on r_anker is not found.
    foreign_table = stage.book("k-f", slot_local, table_id="t_9")
    assert foreign_table.status_code == 404
    assert error_code(foreign_table) == "not_found"


def test_overlapping_reservation_on_the_same_table_conflicts(stage, slot_local):
    stage.book("k-first", slot_local)

    overlapping = stage.book("k-second", f"{stage.thu}T19:30")
    assert overlapping.status_code == 409
    assert error_code(overlapping) == "table_unavailable"


def test_back_to_back_reservations_on_one_table_are_legal(stage, slot_local):
    first = stage.book("k-a", slot_local).json()
    assert first["ends_at"].startswith(f"{stage.thu}T20:30")

    second = stage.book("k-b", f"{stage.thu}T20:30")
    assert second.status_code == 201, second.text


def test_a_free_table_can_be_booked_in_parallel(stage, slot_local):
    assert stage.book("k-t2", slot_local, table_id="t_2").status_code == 201
    assert stage.book("k-t3", slot_local, table_id="t_3").status_code == 201


# ---------------------------------------------------------------------------
# GET /reservations and GET /reservations/{reference}
# ---------------------------------------------------------------------------

def test_list_returns_the_callers_reservations_newest_first(stage):
    early = stage.reference("k-early", f"{stage.thu}T18:00")
    later = stage.reference("k-later", f"{stage.thu}T21:00")
    stage.cancel(later)

    listed = stage.client.get("/reservations", headers=stage.headers()).json()["reservations"]
    starts = [item["starts_at"] for item in listed]
    assert starts == sorted(starts, reverse=True)
    assert {item["reference"] for item in listed} == {early, later}
    assert {item["status"] for item in listed} == {"confirmed", "cancelled"}

    other = stage.client.get("/reservations", headers=stage.headers("u_ben")).json()
    assert other["reservations"] == []


def test_listing_is_empty_for_a_fresh_caller(stage):
    body = stage.client.get("/reservations", headers=stage.headers("u_ben")).json()
    assert body == {"reservations": []}


def test_reading_another_diners_reservation_does_not_leak(stage, slot_local):
    reference = stage.reference("k-own", slot_local)

    assert stage.reservation(reference).status_code == 200
    assert stage.reservation(reference, user_id="u_ben").status_code == 404
    assert error_code(stage.reservation(reference, user_id="u_ben")) == "not_found"


def test_reading_an_unknown_reference_is_not_found(stage):
    response = stage.reservation("ZZZZZZ")
    assert response.status_code == 404
    assert error_code(response) == "not_found"


# ---------------------------------------------------------------------------
# POST /reservations/{reference}/cancel
# ---------------------------------------------------------------------------

def test_cancel_returns_the_current_state_and_frees_the_table(stage, slot_local):
    reference = stage.reference("k-cancel", slot_local)

    response = stage.cancel(reference)
    assert response.status_code == 200
    assert response.json()["reference"] == reference
    assert response.json()["status"] == "cancelled"

    slot = next(item for item in stage.availability(party_size=4).json()["slots"]
                if item["starts_at_local"] == slot_local)
    assert "t_2" in slot["available_table_ids"]


def test_cancelling_twice_is_not_an_error(stage, slot_local):
    reference = stage.reference("k-twice", slot_local)

    assert stage.cancel(reference).status_code == 200
    second = stage.cancel(reference)
    assert second.status_code == 200
    assert second.json()["status"] == "cancelled"


def test_cancel_inside_the_cutoff_is_rejected(stage):
    reference = inside_cutoff(stage)

    response = stage.cancel(reference)
    assert response.status_code == 409
    assert error_code(response) == "cutoff_passed"


def test_cancelling_someone_elses_reservation_is_not_found(stage, slot_local):
    reference = stage.reference("k-other", slot_local)

    response = stage.cancel(reference, user_id="u_ben")
    assert response.status_code == 404
    assert error_code(response) == "not_found"


# ---------------------------------------------------------------------------
# PATCH /reservations/{reference}
# ---------------------------------------------------------------------------

def test_amendment_keeps_identity_and_moves_occupancy(stage, slot_local):
    created = stage.book("k-amend", slot_local).json()

    # 21:00-22:30 does not reach back into the 19:00-20:30 window the booking
    # vacates, so that window must be bookable again afterwards.
    response = stage.amend(created["reference"], starts_at_local=f"{stage.thu}T21:00")
    assert response.status_code == 200, response.text

    amended = response.json()
    assert amended["reference"] == created["reference"]
    assert amended["reservation_id"] == created["reservation_id"]
    assert amended["starts_at_local"] == f"{stage.thu}T21:00"
    assert amended["created_at"] == created["created_at"]

    slot = next(item for item in stage.availability(party_size=4).json()["slots"]
                if item["starts_at_local"] == slot_local)
    assert "t_2" in slot["available_table_ids"]


def test_amendment_onto_its_own_released_window_is_allowed(stage, slot_local):
    reference = stage.reference("k-self", slot_local)

    assert stage.amend(reference, starts_at_local=f"{stage.thu}T20:00").status_code == 200
    back = stage.amend(reference, starts_at_local=slot_local)
    assert back.status_code == 200, back.text


def test_failed_amendment_leaves_the_reservation_untouched(stage, slot_local):
    reference = stage.reference("k-keep", slot_local)
    before = stage.reservation(reference).json()

    stage.book("k-block", f"{stage.thu}T20:00", table_id="t_3")
    conflict = stage.amend(reference, table_id="t_3", starts_at_local=f"{stage.thu}T20:00")
    assert conflict.status_code == 409
    assert error_code(conflict) == "table_unavailable"
    assert stage.reservation(reference).json() == before

    invalid = stage.amend(reference, starts_at_local=f"{stage.thu}T19:07")
    assert invalid.status_code == 422
    assert stage.reservation(reference).json() == before


def test_amendment_uses_the_same_rules_as_creation(stage, slot_local):
    reference = stage.reference("k-rules", slot_local)

    over_capacity = stage.amend(reference, party_size=9)
    assert over_capacity.status_code == 422
    assert error_code(over_capacity) == "party_exceeds_capacity"

    too_large = stage.amend(reference, party_size=0)
    assert too_large.status_code == 422
    assert error_code(too_large) == "validation_failed"


def test_amendment_of_a_cancelled_reservation_is_rejected(stage, slot_local):
    reference = stage.reference("k-cancelled", slot_local)
    stage.cancel(reference)

    response = stage.amend(reference, party_size=2)
    assert response.status_code == 409
    assert error_code(response) == "reservation_cancelled"


def test_amendment_inside_the_cutoff_is_rejected(stage):
    reference = inside_cutoff(stage)

    response = stage.amend(reference, party_size=3)
    assert response.status_code == 409
    assert error_code(response) == "cutoff_passed"


def test_amendment_ignores_unknown_fields(stage, slot_local):
    reference = stage.reference("k-ignore", slot_local)

    response = stage.amend(reference, starts_at_local=f"{stage.thu}T20:00", mood="calm")
    assert response.status_code == 200, response.text
    assert "mood" not in response.json()
