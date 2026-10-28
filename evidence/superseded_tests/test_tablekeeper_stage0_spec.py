"""Archived, not collected: `norecursedirs = evidence` in the root `pytest.ini`.

These 19 tests shipped with commit `9a56d71` and failed 19/19 against the app in
the same commit. They encode the October 3 contract — the "NEW SPEC" at
`room.json` message index 110, which `evidence/state.json` records verbatim as
`run.spec` for the delivered run `run-1791009577173` (uppercase codes such as
`EMAIL_TAKEN`, `POST /_test/reset` -> `200 {"status": "reset"}`, a `name` signup
field, `RES-XXXXXXXX` references). The service instead implements the October 1
brief (`room.json` index 40: lowercase codes, 204 reset, `display_name`,
`[A-Z0-9]` references), which the live suite under `stage-1/tests/` targets.

Neither contract has been declared authoritative, so the directory name
`superseded_tests` records the file's status in the tree, not a proven
supersession of the requirements it encodes. The tests are kept byte-identical
as provenance of that unresolved conflict; see "The test suite was rewritten" in
the top-level README.
"""
import pytest
from datetime import datetime, timezone, timedelta
import concurrent.futures

def test_health_and_reset(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}

    res2 = client.post("/_test/reset")
    assert res2.status_code == 200
    assert res2.json() == {"status": "reset"}

def test_export_import_roundtrip(client):
    signup = client.post("/auth/signup", json={"email": "alice@example.com", "password": "password123", "name": "Alice"})
    assert signup.status_code == 201
    token = signup.json()["token"]

    export_res = client.get("/_test/export")
    assert export_res.status_code == 200
    state = export_res.json()
    assert len(state["restaurants"]) == 3
    assert len(state["users"]) == 1

    client.post("/_test/reset")
    assert len(client.get("/_test/export").json()["users"]) == 0

    import_res = client.post("/_test/import", json=state)
    assert import_res.status_code == 200
    assert import_res.json() == {"status": "imported"}

    res_list = client.get("/reservations", headers={"Authorization": f"Bearer {token}"})
    assert res_list.status_code == 200

def test_auth_validation_and_duplication(client):
    r1 = client.post("/auth/signup", json={"email": "Bob@Example.com", "password": "secret", "name": "Bob"})
    assert r1.status_code == 201
    data = r1.json()
    assert "token" in data

    r2 = client.post("/auth/signup", json={"email": "bob@example.com", "password": "other", "name": "Bobby"})
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "EMAIL_TAKEN"

    r3 = client.post("/auth/login", json={"email": "bob@example.com", "password": "wrong"})
    assert r3.status_code == 401
    assert r3.json()["error"]["code"] == "INVALID_CREDENTIALS"

    r4 = client.post("/auth/login", json={"email": "BOB@EXAMPLE.COM", "password": "secret"})
    assert r4.status_code == 200

    r5 = client.get("/reservations")
    assert r5.status_code == 401
    assert r5.json()["error"]["code"] == "UNAUTHENTICATED"

def test_restaurants_endpoints(client):
    res = client.get("/restaurants")
    assert res.status_code == 200
    data = res.json()
    assert len(data["restaurants"]) == 3
    assert data["restaurants"][0]["id"] == "r-berlin"

    det = client.get("/restaurants/r-berlin")
    assert det.status_code == 200
    assert det.json()["name"] == "Speisekammer Mitte"
    assert len(det.json()["tables"]) == 4

    res_404 = client.get("/restaurants/r-nonexistent")
    assert res_404.status_code == 404
    assert res_404.json()["error"]["code"] == "UNKNOWN_RESTAURANT"

def test_availability_and_party_size(client):
    future_date = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    
    too_large = client.get(f"/availability?restaurant_id=r-berlin&date={future_date}&party_size=10")
    assert too_large.status_code == 400
    assert too_large.json()["error"]["code"] == "PARTY_TOO_LARGE"

    avail = client.get(f"/availability?restaurant_id=r-berlin&date={future_date}&party_size=2")
    assert avail.status_code == 200
    assert len(avail.json()["slots"]) > 0

def test_reservation_creation_errors(client):
    signup = client.post("/auth/signup", json={"email": "charlie@example.com", "password": "pwd", "name": "Charlie"})
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    bad_ts = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": "2027-06-01T12:00:00",
        "party_size": 2
    })
    assert bad_ts.status_code == 400
    assert bad_ts.json()["error"]["code"] == "INVALID_TIMESTAMP"

    past_ts = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": "2020-01-01T12:00:00Z",
        "party_size": 2
    })
    assert past_ts.status_code == 400
    assert past_ts.json()["error"]["code"] == "IN_THE_PAST"

    gap_res = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": "2027-03-28T02:30:00+01:00",
        "party_size": 2
    })
    assert gap_res.status_code == 400
    assert gap_res.json()["error"]["code"] == "NONEXISTENT_LOCAL_TIME"

def test_half_open_intervals_and_back_to_back(client):
    signup = client.post("/auth/signup", json={"email": "frank@example.com", "password": "pwd", "name": "Frank"})
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Pick a future slot e.g. 3 days from now at 09:00 UTC (11:00 Berlin time)
    future_dt = (datetime.now(timezone.utc) + timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()

    # Create first reservation on table A (max_seats 2)
    r1 = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": future_dt,
        "duration_minutes": 90,
        "party_size": 2,
        "table_id": "t-berlin-a"
    })
    assert r1.status_code == 201

    # Overlapping reservation on same table -> 409 TABLE_UNAVAILABLE
    overlap_dt = (datetime.fromisoformat(future_dt) + timedelta(minutes=30)).isoformat()
    r_overlap = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": overlap_dt,
        "duration_minutes": 90,
        "party_size": 2,
        "table_id": "t-berlin-a"
    })
    assert r_overlap.status_code == 409
    assert r_overlap.json()["error"]["code"] == "TABLE_UNAVAILABLE"

    # Back-to-back reservation starting exactly when first ends (12:00 + 90m = 13:30) -> 201
    back_to_back_dt = (datetime.fromisoformat(future_dt) + timedelta(minutes=90)).isoformat()
    r_b2b = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": back_to_back_dt,
        "duration_minutes": 90,
        "party_size": 2,
        "table_id": "t-berlin-a"
    })
    assert r_b2b.status_code == 201

def test_cancellation_lifecycle(client):
    signup = client.post("/auth/signup", json={"email": "grace@example.com", "password": "pwd", "name": "Grace"})
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    future_dt = (datetime.now(timezone.utc) + timedelta(days=5)).replace(hour=11, minute=0, second=0, microsecond=0).isoformat()

    r1 = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": future_dt,
        "party_size": 2,
        "table_id": "t-berlin-a"
    })
    assert r1.status_code == 201
    ref = r1.json()["reference"]

    # Cancel
    cancel = client.post(f"/reservations/{ref}/cancel", headers=headers)
    assert cancel.status_code == 200

    # Re-cancel -> 409 ALREADY_CANCELLED
    re_cancel = client.post(f"/reservations/{ref}/cancel", headers=headers)
    assert re_cancel.status_code == 409
    assert re_cancel.json()["error"]["code"] == "ALREADY_CANCELLED"

    # Slot now bookable again by another reservation on same table
    r2 = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": future_dt,
        "party_size": 2,
        "table_id": "t-berlin-a"
    })
    assert r2.status_code == 201

def test_patch_reservation(client):
    signup = client.post("/auth/signup", json={"email": "helen@example.com", "password": "pwd", "name": "Helen"})
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    future_dt1 = (datetime.now(timezone.utc) + timedelta(days=6)).replace(hour=18, minute=0, second=0, microsecond=0).isoformat()
    r1 = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": future_dt1,
        "party_size": 2,
        "table_id": "t-berlin-a"
    })
    assert r1.status_code == 201
    ref = r1.json()["reference"]

    # Patch duration / party size
    patch_res = client.post(f"/reservations/{ref}", headers=headers) # wait, patch is PATCH
    # Let's use client.patch
    patch_res = client.patch(f"/reservations/{ref}", headers=headers, json={
        "duration_minutes": 120
    })
    assert patch_res.status_code == 200
    assert patch_res.json()["duration_minutes"] == 120

def test_reservation_moves_swap(client):
    signup = client.post("/auth/signup", json={"email": "ian@example.com", "password": "pwd", "name": "Ian"})
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    dt_slot = (datetime.now(timezone.utc) + timedelta(days=7)).replace(hour=19, minute=0, second=0, microsecond=0).isoformat()

    # Create two reservations on table A and table B at the same time
    rA = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": dt_slot,
        "party_size": 2,
        "table_id": "t-berlin-a"
    })
    assert rA.status_code == 201
    refA = rA.json()["reference"]

    rB = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": dt_slot,
        "party_size": 2,
        "table_id": "t-berlin-b"
    })
    assert rB.status_code == 201
    refB = rB.json()["reference"]

    # Swap tables using /reservation-moves
    moves_res = client.post("/reservation-moves", headers=headers, json={
        "moves": [
            {"reference": refA, "table_id": "t-berlin-b"},
            {"reference": refB, "table_id": "t-berlin-a"}
        ]
    })
    assert moves_res.status_code == 200
    moved_list = moves_res.json()["moved"]
    assert len(moved_list) == 2

def test_idempotency_behavior(client):
    signup = client.post("/auth/signup", json={"email": "eve@example.com", "password": "pwd", "name": "Eve"})
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "my-key-123"}

    future_dt = (datetime.now(timezone.utc) + timedelta(days=4)).replace(hour=17, minute=30, second=0, microsecond=0).isoformat()
    payload = {
        "restaurant_id": "r-berlin",
        "starts_at": future_dt,
        "party_size": 2
    }

    r1 = client.post("/reservations", headers=headers, json=payload)
    assert r1.status_code == 201
    ref1 = r1.json()["reference"]

    r2 = client.post("/reservations", headers=headers, json=payload)
    assert r2.status_code == 201
    assert r2.headers.get("Idempotency-Replayed") == "true"
    assert r2.json()["reference"] == ref1

    bad_payload = dict(payload)
    bad_payload["party_size"] = 4
    r3 = client.post("/reservations", headers=headers, json=bad_payload)
    assert r3.status_code == 409
    assert r3.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

def test_malformed_json(client):
    res = client.post("/auth/signup", content="not json", headers={"Content-Type": "application/json"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "MALFORMED_JSON"

def test_dst_fallback_and_crossing(client):
    from app.db import get_connection
    with get_connection() as conn:
        conn.execute("INSERT INTO opening_hours (restaurant_id, weekday, opens, closes) VALUES ('r-berlin', 6, '01:00', '04:00')")
        conn.commit()

    signup = client.post("/auth/signup", json={"email": "dst@example.com", "password": "pwd", "name": "DST"})
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    fallback_dt_str = "2026-10-25T02:30:00+02:00"
    r = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": fallback_dt_str,
        "duration_minutes": 90,
        "party_size": 2
    })
    assert r.status_code == 201
    data = r.json()
    start_utc = datetime.fromisoformat(data["starts_at"])
    end_utc = datetime.fromisoformat(data["ends_at"])
    assert (end_utc - start_utc) == timedelta(minutes=90)

def test_concurrency_same_key_and_different_keys(client):
    signup = client.post("/auth/signup", json={"email": "conc@example.com", "password": "pwd", "name": "Conc"})
    token = signup.json()["token"]
    
    future_dt = (datetime.now(timezone.utc) + timedelta(days=10)).replace(hour=18, minute=0, second=0, microsecond=0).isoformat()
    payload = {
        "restaurant_id": "r-berlin",
        "starts_at": future_dt,
        "party_size": 2,
        "table_id": "t-berlin-a"
    }

    def make_req_same_key(i):
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "shared-key-999"}
        return client.post("/reservations", headers=headers, json=payload)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_req_same_key, i) for i in range(5)]
        results = [f.result() for f in futures]
    
    status_codes = [r.status_code for r in results]
    assert status_codes.count(201) == 5

    signup2 = client.post("/auth/signup", json={"email": "conc2@example.com", "password": "pwd", "name": "Conc2"})
    token2 = signup2.json()["token"]

    future_dt2 = (datetime.now(timezone.utc) + timedelta(days=11)).replace(hour=19, minute=0, second=0, microsecond=0).isoformat()
    payload2 = {
        "restaurant_id": "r-berlin",
        "starts_at": future_dt2,
        "party_size": 2,
        "table_id": "t-berlin-a"
    }

    def make_req_diff_key(i):
        headers = {"Authorization": f"Bearer {token2}", "Idempotency-Key": f"diff-key-{i}"}
        return client.post("/reservations", headers=headers, json=payload2)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_req_diff_key, i) for i in range(5)]
        results = [f.result() for f in futures]

    status_codes_diff = [r.status_code for r in results]
    assert status_codes_diff.count(201) == 1
    assert status_codes_diff.count(409) == 4

def test_unknown_fields_ignored(client):
    signup = client.post("/auth/signup", json={"email": "ignore@example.com", "password": "pwd", "name": "Ignore", "extra_field": "should_be_ignored"})
    assert signup.status_code == 201
    token = signup.json()["token"]

    res = client.get(f"/restaurants/r-berlin?unknown_query=123")
    assert res.status_code == 200

def test_idempotency_on_patch_and_moves(client):
    signup = client.post("/auth/signup", json={"email": "patchmove@example.com", "password": "pwd", "name": "PM"})
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    future_dt = (datetime.now(timezone.utc) + timedelta(days=12)).replace(hour=10, minute=0, second=0, microsecond=0).isoformat()
    r = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": future_dt,
        "party_size": 2,
        "table_id": "t-berlin-a"
    })
    assert r.status_code == 201
    ref = r.json()["reference"]

    # Test PATCH idempotency
    patch_payload = {"duration_minutes": 120}
    patch_headers = dict(headers)
    patch_headers["Idempotency-Key"] = "patch-key-1"

    p1 = client.patch(f"/reservations/{ref}", headers=patch_headers, json=patch_payload)
    assert p1.status_code == 200
    assert p1.json()["duration_minutes"] == 120

    p2 = client.patch(f"/reservations/{ref}", headers=patch_headers, json=patch_payload)
    assert p2.status_code == 200
    assert p2.headers.get("Idempotency-Replayed") == "true"
    assert p2.json()["duration_minutes"] == 120

    bad_patch_payload = {"duration_minutes": 150}
    p3 = client.patch(f"/reservations/{ref}", headers=patch_headers, json=bad_patch_payload)
    assert p3.status_code == 409
    assert p3.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    # Test reservation-moves idempotency
    future_dt2 = (datetime.now(timezone.utc) + timedelta(days=13)).replace(hour=10, minute=0, second=0, microsecond=0).isoformat()
    r2 = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": future_dt2,
        "party_size": 2,
        "table_id": "t-berlin-b"
    })
    assert r2.status_code == 201
    ref2 = r2.json()["reference"]

    move_payload = {
        "moves": [{"reference": ref2, "table_id": "t-berlin-c"}]
    }
    move_headers = dict(headers)
    move_headers["Idempotency-Key"] = "move-key-1"

    m1 = client.post("/reservation-moves", headers=move_headers, json=move_payload)
    assert m1.status_code == 200
    assert m1.json()["moved"][0]["table_id"] == "t-berlin-c"

    m2 = client.post("/reservation-moves", headers=move_headers, json=move_payload)
    assert m2.status_code == 200
    assert m2.headers.get("Idempotency-Replayed") == "true"
    assert m2.json()["moved"][0]["table_id"] == "t-berlin-c"

    bad_move_payload = {
        "moves": [{"reference": ref2, "table_id": "t-berlin-a"}]
    }
    m3 = client.post("/reservation-moves", headers=move_headers, json=bad_move_payload)
    assert m3.status_code == 409
    assert m3.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

def test_idempotency_receipt_export_import(client):
    signup = client.post("/auth/signup", json={"email": "exportrec@example.com", "password": "pwd", "name": "ER"})
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "export-key-777"}

    future_dt = (datetime.now(timezone.utc) + timedelta(days=14)).replace(hour=11, minute=0, second=0, microsecond=0).isoformat()
    payload = {
        "restaurant_id": "r-berlin",
        "starts_at": future_dt,
        "party_size": 2
    }

    r1 = client.post("/reservations", headers=headers, json=payload)
    assert r1.status_code == 201
    ref1 = r1.json()["reference"]

    state = client.get("/_test/export").json()
    assert len(state["idempotency_receipts"]) > 0

    client.post("/_test/reset")
    assert len(client.get("/_test/export").json()["idempotency_receipts"]) == 0

    import_res = client.post("/_test/import", json=state)
    assert import_res.status_code == 200

    # Replay with same key should work after import
    r2 = client.post("/reservations", headers=headers, json=payload)
    assert r2.status_code == 201
    assert r2.headers.get("Idempotency-Replayed") == "true"
    assert r2.json()["reference"] == ref1

def test_reservation_moves_atomic_failure(client):
    signup = client.post("/auth/signup", json={"email": "moveerr@example.com", "password": "pwd", "name": "ME"})
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    future_dt = (datetime.now(timezone.utc) + timedelta(days=15)).replace(hour=11, minute=0, second=0, microsecond=0).isoformat()
    r1 = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": future_dt,
        "party_size": 2,
        "table_id": "t-berlin-a"
    })
    assert r1.status_code == 201
    ref1 = r1.json()["reference"]

    # Try moving with unknown reference
    m_fail = client.post("/reservation-moves", headers=headers, json={
        "moves": [
            {"reference": ref1, "table_id": "t-berlin-b"},
            {"reference": "RES-UNKNOWN", "table_id": "t-berlin-a"}
        ]
    })
    assert m_fail.status_code == 404
    assert m_fail.json()["error"]["code"] == "UNKNOWN_REFERENCE"

    # Verify ref1 table was NOT changed (atomic rollback)
    res_check = client.get(f"/reservations/{ref1}", headers=headers)
    assert res_check.status_code == 200
    assert res_check.json()["table_id"] == "t-berlin-a"

def test_opening_hours_and_deviations(client):
    signup = client.post("/auth/signup", json={"email": "devtest@example.com", "password": "pwd", "name": "Dev"})
    token = signup.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Closed-hours create -> 409 NO_AVAILABILITY
    r_closed = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": "2026-10-08T03:00:00+02:00",
        "party_size": 2
    })
    assert r_closed.status_code == 409
    assert r_closed.json()["error"]["code"] == "NO_AVAILABILITY"

    # 2. 13:30/14:00 spill creates -> 409 NO_AVAILABILITY
    r_spill1 = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": "2026-10-08T13:30:00+02:00",
        "duration_minutes": 90,
        "party_size": 2
    })
    assert r_spill1.status_code == 409
    assert r_spill1.json()["error"]["code"] == "NO_AVAILABILITY"

    r_spill2 = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": "2026-10-08T14:00:00+02:00",
        "duration_minutes": 90,
        "party_size": 2
    })
    assert r_spill2.status_code == 409
    assert r_spill2.json()["error"]["code"] == "NO_AVAILABILITY"

    # 3. 240min-from-11:00 -> 409 NO_AVAILABILITY
    r_long = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": "2026-10-08T11:00:00+02:00",
        "duration_minutes": 240,
        "party_size": 2
    })
    assert r_long.status_code == 409
    assert r_long.json()["error"]["code"] == "NO_AVAILABILITY"

    # 4. Boundary 13:00+90min -> 201
    r_boundary = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": "2026-10-08T13:00:00+02:00",
        "duration_minutes": 90,
        "party_size": 2
    })
    assert r_boundary.status_code == 201
    ref = r_boundary.json()["reference"]

    # 5. Unknown table_id -> 409 TABLE_UNAVAILABLE
    r_unknown_table = client.post("/reservations", headers=headers, json={
        "restaurant_id": "r-berlin",
        "starts_at": "2026-10-08T11:00:00+02:00",
        "duration_minutes": 90,
        "party_size": 2,
        "table_id": "t-berlin-z"
    })
    assert r_unknown_table.status_code == 409
    assert r_unknown_table.json()["error"]["code"] == "TABLE_UNAVAILABLE"

    # 6. Patch into closed hours -> 409 NO_AVAILABILITY with reservation unchanged
    r_patch = client.patch(f"/reservations/{ref}", headers=headers, json={
        "starts_at": "2026-10-08T15:00:00+02:00"
    })
    assert r_patch.status_code == 409
    assert r_patch.json()["error"]["code"] == "NO_AVAILABILITY"

    r_get = client.get(f"/reservations/{ref}", headers=headers)
    assert r_get.status_code == 200
    assert r_get.json()["starts_at"].startswith("2026-10-08T11:00:00")

    # Move into closed hours -> 409 NO_AVAILABILITY with reservation unchanged
    r_move = client.post("/reservation-moves", headers=headers, json={
        "moves": [
            {"reference": ref, "starts_at": "2026-10-08T15:00:00+02:00"}
        ]
    })
    assert r_move.status_code == 409
    assert r_move.json()["error"]["code"] == "NO_AVAILABILITY"

    r_get2 = client.get(f"/reservations/{ref}", headers=headers)
    assert r_get2.status_code == 200
    assert r_get2.json()["starts_at"].startswith("2026-10-08T11:00:00")

