"""Idempotency (§7), export/import (§10), atomic moves (§11), time and DST (§9),
and the "no 5xx under concurrent load" rule (§5).

Derived from the Stage 1 requirements quoted in `room.json` only.
"""

import json
from datetime import timedelta

from conftest import fixture_document


def error_code(response):
    assert response.status_code >= 400, response.text
    return response.json()["error"]["code"]


def body_of(response):
    return response.json()


# ---------------------------------------------------------------------------
# §7 idempotency
# ---------------------------------------------------------------------------

def test_idempotency_key_is_required_on_both_write_paths(stage, slot_local):
    missing = stage.client.post("/reservations", headers=stage.headers(), json={
        "restaurant_id": "r_anker", "table_id": "t_2",
        "starts_at_local": slot_local, "party_size": 4})
    assert missing.status_code == 400
    assert error_code(missing) == "missing_idempotency_key"

    empty = stage.client.post("/reservations", headers=stage.headers(key=""), json={
        "restaurant_id": "r_anker", "table_id": "t_2",
        "starts_at_local": slot_local, "party_size": 4})
    assert empty.status_code == 400
    assert error_code(empty) == "missing_idempotency_key"

    reference = stage.reference("k-move-key", slot_local)
    moves_without_key = stage.client.post("/reservation-moves",
                                          headers=stage.headers(),
                                          json={"moves": [{"reference": reference}]})
    assert moves_without_key.status_code == 400
    assert error_code(moves_without_key) == "missing_idempotency_key"


def test_idempotency_key_length_is_bounded(stage, slot_local):
    response = stage.book("x" * 256, slot_local)
    assert response.status_code == 422
    assert error_code(response) == "validation_failed"


def test_replay_returns_the_original_response_and_creates_nothing_more(stage, slot_local):
    payload = {"restaurant_id": "r_anker", "table_id": "t_2",
               "starts_at_local": slot_local, "party_size": 4}
    headers = {"Authorization": f"Bearer {stage.tokens['u_ada']}", "Idempotency-Key": "k-replay"}

    first = stage.client.post("/reservations", headers=headers, json=payload)
    assert first.status_code == 201

    replay = stage.client.post("/reservations", headers=headers, json=payload)
    assert replay.status_code == 200
    assert body_of(replay) == body_of(first)

    listed = stage.client.get("/reservations", headers=stage.headers()).json()["reservations"]
    assert len(listed) == 1


def test_body_equality_ignores_key_order_and_whitespace(stage, slot_local):
    headers = {"Authorization": f"Bearer {stage.tokens['u_ada']}", "Idempotency-Key": "k-order"}
    one = {"restaurant_id": "r_anker", "table_id": "t_2",
           "starts_at_local": slot_local, "party_size": 4}
    other = {"party_size": 4, "starts_at_local": slot_local,
             "table_id": "t_2", "restaurant_id": "r_anker"}

    assert stage.client.post("/reservations", headers=headers, json=one).status_code == 201
    replay = stage.client.post("/reservations",
                               headers={**headers, "Content-Type": "application/json"},
                               content=json.dumps(other, indent=2).encode())
    assert replay.status_code == 200
    listed = stage.client.get("/reservations", headers=stage.headers()).json()["reservations"]
    assert body_of(replay) == listed[0]
    assert len(listed) == 1


def test_same_key_with_a_different_body_conflicts(stage, slot_local):
    assert stage.book("k-reuse", slot_local).status_code == 201

    conflict = stage.book("k-reuse", f"{stage.thu}T20:00")
    assert conflict.status_code == 409
    assert error_code(conflict) == "idempotency_key_reuse"


def test_used_key_with_an_invalid_new_body_still_reports_reuse(stage, slot_local):
    assert stage.book("k-order2", slot_local).status_code == 201

    invalid_body = stage.client.post("/reservations", headers=stage.headers(key="k-order2"),
                                     json={"restaurant_id": "r_anker", "table_id": "t_2",
                                           "starts_at_local": "nonsense", "party_size": 4})
    assert invalid_body.status_code == 409
    assert error_code(invalid_body) == "idempotency_key_reuse"


def test_a_key_is_reusable_after_the_original_request_failed(stage, slot_local):
    rejected = stage.book("k-after-fail", f"{stage.thu}T19:07")
    assert rejected.status_code == 422

    accepted = stage.book("k-after-fail", slot_local)
    assert accepted.status_code == 201, accepted.text


def test_keys_are_scoped_to_the_authenticated_user(stage, slot_local):
    ada = stage.book("k-shared", slot_local)
    assert ada.status_code == 201

    ben = stage.book("k-shared", slot_local, user_id="u_ben", table_id="t_3")
    assert ben.status_code == 201, ben.text
    assert body_of(ben)["reservation_id"] != body_of(ada)["reservation_id"]


def test_same_key_on_a_different_path_is_not_a_replay(stage, slot_local):
    assert stage.book("k-cross", slot_local).status_code == 201

    reference = stage.reference("k-cross-target", f"{stage.thu}T20:00", table_id="t_3")
    moved = stage.move("k-cross", [{"reference": reference, "table_id": "t_2",
                                    "starts_at_local": f"{stage.thu}T21:00"}])
    # The receipts slot is scoped to (user, method, path, key), so /reservation-moves
    # has never seen this key and the batch runs normally.
    assert moved.status_code == 201, moved.text

    # The /reservations slot is still its own: replaying there replays.
    replay = stage.book("k-cross", slot_local)
    assert replay.status_code == 200


# ---------------------------------------------------------------------------
# §11 atomic reservation moves
# ---------------------------------------------------------------------------

def test_moves_swap_two_tables_and_return_reservations_in_input_order(stage, slot_local):
    first = stage.reference("k-m1", slot_local, table_id="t_2", party_size=2)
    second = stage.reference("k-m2", slot_local, table_id="t_3", party_size=4)

    response = stage.move("k-swap", [{"reference": first, "table_id": "t_3"},
                                     {"reference": second, "table_id": "t_2"}])
    assert response.status_code == 201, response.text

    moved = response.json()["reservations"]
    assert [item["reference"] for item in moved] == [first, second]
    assert [item["table_id"] for item in moved] == ["t_3", "t_2"]
    assert all(item["starts_at_local"] == slot_local for item in moved)


def test_moves_replay_returns_the_original_batch_with_200(stage, slot_local):
    first = stage.reference("k-m3", slot_local, table_id="t_2", party_size=2)
    second = stage.reference("k-m4", slot_local, table_id="t_3", party_size=4)
    moves = [{"reference": first, "table_id": "t_3"},
             {"reference": second, "table_id": "t_2"}]

    created = stage.move("k-swap2", moves)
    assert created.status_code == 201

    replay = stage.move("k-swap2", moves)
    assert replay.status_code == 200
    assert body_of(replay) == body_of(created)


def test_moves_validate_shape_before_touching_anything(stage, slot_local):
    reference = stage.reference("k-m5", slot_local)
    before = stage.reservation(reference).json()

    empty = stage.move("k-empty", [])
    assert empty.status_code == 422
    assert error_code(empty) == "validation_failed"

    duplicated = stage.move("k-dup", [{"reference": reference, "table_id": "t_3"},
                                      {"reference": reference, "table_id": "t_1"}])
    assert duplicated.status_code == 422
    assert error_code(duplicated) == "validation_failed"
    assert stage.reservation(reference).json() == before


def test_moves_of_an_unknown_or_foreign_reference_apply_nothing(stage, slot_local):
    reference = stage.reference("k-m6", slot_local, table_id="t_2")
    other = stage.reference("k-m7", f"{stage.thu}T20:00", table_id="t_3")
    before_first = stage.reservation(reference).json()

    response = stage.move("k-missing", [{"reference": reference, "table_id": "t_3"},
                                       {"reference": "ZZZZZZ", "table_id": "t_1"}])
    assert response.status_code == 404
    assert error_code(response) == "not_found"
    assert stage.reservation(reference).json() == before_first
    assert stage.reservation(other, user_id="u_ben").status_code == 404

    foreign = stage.move("k-foreign", [{"reference": other, "table_id": "t_1"}],
                         user_id="u_ben")
    assert foreign.status_code == 404


def test_moves_across_restaurants_are_rejected(stage, slot_local):
    here = stage.reference("k-x1", slot_local, table_id="t_2", party_size=2)
    there = stage.reference("k-x2", f"{stage.thu}T20:00", restaurant_id="r_late",
                            table_id="t_9", party_size=2)

    response = stage.move("k-x3", [{"reference": here, "table_id": "t_9"},
                                   {"reference": there, "table_id": "t_2"}])
    assert response.status_code == 422
    assert error_code(response) == "validation_failed"

    # Nothing moved.
    assert stage.reservation(here).json()["table_id"] == "t_2"
    assert stage.reservation(there).json()["table_id"] == "t_9"


def test_moves_conflicting_with_an_unlisted_booking_apply_nothing(stage, slot_local):
    mover = stage.reference("k-c1", f"{stage.thu}T20:00", table_id="t_3", party_size=4)
    blocker = stage.reference("k-c2", slot_local, table_id="t_2", party_size=4)

    response = stage.move("k-c3", [{"reference": mover, "table_id": "t_2",
                                    "starts_at_local": slot_local}])
    assert response.status_code == 409
    assert error_code(response) == "table_unavailable"
    assert stage.reservation(mover).json()["table_id"] == "t_3"
    assert stage.reservation(blocker).json()["table_id"] == "t_2"


def test_moves_keep_omitted_fields_and_ignore_unknown_ones(stage, slot_local):
    reference = stage.reference("k-keep2", slot_local, table_id="t_2", party_size=4)

    response = stage.move("k-keep3", [{"reference": reference, "notes": "irrelevant"}])
    assert response.status_code == 201, response.text

    unchanged = response.json()["reservations"][0]
    assert unchanged["table_id"] == "t_2"
    assert unchanged["party_size"] == 4
    assert unchanged["starts_at_local"] == slot_local
    assert "notes" not in unchanged


def test_moves_of_a_cancelled_booking_are_rejected(stage, slot_local):
    reference = stage.reference("k-cancel-move", slot_local)
    stage.cancel(reference)

    response = stage.move("k-cancel-move2", [{"reference": reference, "table_id": "t_3"}])
    assert response.status_code == 409
    assert error_code(response) == "reservation_cancelled"


# ---------------------------------------------------------------------------
# §10 export and import
# ---------------------------------------------------------------------------

def test_export_has_the_envelope_and_import_returns_no_content(stage, slot_local):
    stage.book("k-export", slot_local)

    export = stage.client.get("/_test/export")
    assert export.status_code == 200
    document = export.json()
    assert document["track"] == "tablekeeper"
    assert document["format_version"] == 1
    assert isinstance(document["state"], dict)

    imported = stage.client.post("/_test/import", json=document)
    assert imported.status_code == 204
    assert imported.content == b""


def test_import_preserves_tokens_references_and_receipts(stage, slot_local):
    created = stage.book("k-roundtrip", slot_local).json()
    reference = created["reference"]
    document = stage.client.get("/_test/export").json()

    stage.reset(fixture_document(reservations=[]))
    assert stage.reservation(reference).status_code == 401  # tokens were wiped

    assert stage.client.post("/_test/import", json=document).status_code == 204

    restored = stage.reservation(reference)
    assert restored.status_code == 200
    assert restored.json()["reservation_id"] == created["reservation_id"]
    assert restored.json()["created_at"] == created["created_at"]

    replay = stage.book("k-roundtrip", slot_local)
    assert replay.status_code == 200
    assert body_of(replay) == created

    assert stage.login("ada@example.com", "correct horse")


def test_import_rejects_a_document_that_does_not_fit_and_changes_nothing(stage, slot_local):
    reference = stage.reference("k-keep-state", slot_local)
    document = stage.client.get("/_test/export").json()

    for broken, label in [
        ({**document, "track": "other"}, "wrong track"),
        ({**document, "format_version": 99}, "wrong version"),
        ({"track": "tablekeeper", "format_version": 1}, "missing state"),
    ]:
        response = stage.client.post("/_test/import", json=broken)
        assert response.status_code == 422, label
        assert error_code(response) == "validation_failed"

    assert stage.reservation(reference).status_code == 200


def test_reset_after_import_clears_everything(stage, slot_local):
    reference = stage.reference("k-clear", slot_local)
    document = stage.client.get("/_test/export").json()
    stage.client.post("/_test/import", json=document)

    assert stage.reservation(reference).status_code == 200
    assert stage.reset().status_code == 204

    # Reset clears all state, including the imported state and its credentials, so
    # the bearer token from before the reset no longer authenticates.
    assert stage.reservation(reference).status_code == 401
    token = stage.login("ada@example.com", "correct horse")
    listed = stage.client.get("/reservations",
                              headers={"Authorization": f"Bearer {token}"}).json()
    assert listed["reservations"] == []
    # The reset re-applied the fixture, so the catalogue is back even though the
    # imported bookings, users and receipts are gone.
    assert stage.client.get("/restaurants/r_anker").json()["id"] == "r_anker"


# ---------------------------------------------------------------------------
# §9 time and DST
# ---------------------------------------------------------------------------

SPRING_FORWARD = "2026-03-29"   # Europe/Berlin 02:00 -> 03:00, a Sunday
FALL_BACK = "2026-10-25"        # Europe/Berlin 03:00 -> 02:00, a Sunday


def test_spring_forward_times_are_never_offered(stage):
    body = stage.availability(day=SPRING_FORWARD, party_size=2).json()
    starts = [slot["starts_at_local"] for slot in body["slots"]]

    assert f"{SPRING_FORWARD}T01:00" in starts
    assert f"{SPRING_FORWARD}T02:00" not in starts
    assert f"{SPRING_FORWARD}T02:30" not in starts
    assert starts == sorted(set(starts))


def test_booking_a_nonexistent_local_time_is_rejected(stage):
    response = stage.book("k-gap", f"{SPRING_FORWARD}T02:30")
    assert response.status_code == 422
    assert error_code(response) == "invalid_local_time"


def test_fall_back_resolves_to_the_first_occurrence_with_absolute_duration(stage):
    response = stage.book("k-fallback", f"{FALL_BACK}T02:30")
    assert response.status_code == 201, response.text

    body = response.json()
    assert body["starts_at"] == f"{FALL_BACK}T02:30:00+02:00"

    from datetime import datetime

    start = datetime.fromisoformat(body["starts_at"])
    end = datetime.fromisoformat(body["ends_at"])
    assert end - start == timedelta(minutes=90)
    assert body["ends_at"] == f"{FALL_BACK}T03:00:00+01:00"


def test_the_repeated_hour_is_offered_once_and_bookable_once(stage):
    body = stage.availability(day=FALL_BACK, party_size=2).json()
    starts = [slot["starts_at_local"] for slot in body["slots"]]

    assert starts.count(f"{FALL_BACK}T02:30") == 1

    assert stage.book("k-repeat", f"{FALL_BACK}T02:30", table_id="t_2").status_code == 201
    # The second occurrence of the wall time is not bookable: both requests resolve
    # to the first one, so the same table is simply already taken.
    second = stage.book("k-repeat2", f"{FALL_BACK}T02:30", table_id="t_2")
    assert second.status_code == 409
    assert error_code(second) == "table_unavailable"


# ---------------------------------------------------------------------------
# §5 / §7 concurrency
# ---------------------------------------------------------------------------

def test_concurrent_identical_requests_take_effect_once(stage, slot_local):
    from concurrent.futures import ThreadPoolExecutor

    payload = {"restaurant_id": "r_anker", "table_id": "t_2",
               "starts_at_local": slot_local, "party_size": 4}
    headers = {"Authorization": f"Bearer {stage.tokens['u_ada']}", "Idempotency-Key": "k-race"}

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(
            lambda _: stage.client.post("/reservations", headers=headers, json=payload),
            range(8)))

    statuses = sorted(response.status_code for response in responses)
    assert 500 not in statuses, statuses
    assert statuses.count(201) == 1, statuses
    assert set(statuses) <= {201, 200}, statuses

    listed = stage.client.get("/reservations", headers=stage.headers()).json()["reservations"]
    assert len(listed) == 1


def test_concurrent_distinct_keys_never_double_book_a_table(stage, slot_local):
    from concurrent.futures import ThreadPoolExecutor

    def attempt(index):
        return stage.book(f"k-slot-{index}", slot_local, party_size=4)

    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(attempt, range(6)))

    statuses = sorted(response.status_code for response in responses)
    assert 500 not in statuses, statuses
    assert statuses.count(201) == 1, statuses

    slot = next(item for item in stage.availability(party_size=4).json()["slots"]
                if item["starts_at_local"] == slot_local)
    assert slot["available_table_ids"] == ["t_3"]
