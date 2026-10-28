"""HTTP-layer rules from the Stage 1 requirements: health, reset, error model (§5),
conventions (§3.4) and authentication (§6).

Derived from the Stage 1 requirements quoted in `room.json` only.
"""

import re

from conftest import fixture_document


def error_code(response):
    assert response.status_code >= 400, response.text
    body = response.json()
    assert set(body) == {"error"}, f"§5 envelope is {{error: ...}}: {body}"
    assert set(body["error"]) >= {"code", "message"}, body
    return body["error"]["code"]


# ---------------------------------------------------------------------------
# §3.2 / §3.3
# ---------------------------------------------------------------------------

def test_health_is_ok_without_auth(raw_client):
    response = raw_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_reset_returns_no_content_and_seeds_the_fixture(stage):
    response = stage.reset()
    assert response.status_code == 204
    assert response.content == b""

    listed = stage.client.get("/restaurants").json()["restaurants"]
    assert {item["id"] for item in listed} == {"r_anker", "r_late"}


def test_reset_can_be_repeated_and_replaces_all_state(stage):
    stage.book("k-seed", f"{stage.thu}T19:00")

    other = fixture_document()
    other["restaurants"] = [dict(other["restaurants"][0], id="r_other", name="Andere")]
    assert stage.reset(other).status_code == 204

    assert stage.client.get("/restaurants/r_anker").status_code == 404
    assert stage.client.get("/restaurants/r_other").status_code == 200

    # §3.3: only the new fixture is visible, so the reservations and the tokens
    # issued before the reset are gone with the state they belonged to.
    assert stage.client.get("/reservations", headers=stage.headers()).status_code == 401
    token = stage.login("ada@example.com", "correct horse")
    listed = stage.client.get("/reservations",
                              headers={"Authorization": f"Bearer {token}"}).json()
    assert listed["reservations"] == []


def test_seeded_users_can_log_in_immediately(stage):
    token = stage.login("ada@example.com", "correct horse")
    assert isinstance(token, str) and token

    response = stage.client.get("/availability", params={
        "restaurant_id": "r_anker", "date": stage.thu, "party_size": 2})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# §3.4 conventions
# ---------------------------------------------------------------------------

def test_unknown_restaurant_uses_the_error_envelope(stage):
    response = stage.client.get("/restaurants/r-nope")
    assert response.status_code == 404
    assert error_code(response) == "not_found"


def test_unknown_query_parameters_are_ignored(stage):
    response = stage.availability(trace="1", foo="bar")
    assert response.status_code == 200


def test_restaurant_detail_carries_the_fixture_shape(stage):
    detail = stage.client.get("/restaurants/r_anker").json()

    assert detail["id"] == "r_anker"
    assert detail["timezone"] == "Europe/Berlin"
    assert detail["slot_minutes"] == 30
    assert detail["reservation_duration_minutes"] == 90
    assert detail["cancellation_cutoff_minutes"] == 120
    assert detail["tables"] == [
        {"id": "t_1", "label": "1", "capacity": 2},
        {"id": "t_2", "label": "2", "capacity": 4},
        {"id": "t_3", "label": "3", "capacity": 4},
    ]
    assert {"weekday": "thu", "opens": "18:00", "closes": "23:00"} in detail["opening_hours"]


# ---------------------------------------------------------------------------
# §5 error model
# ---------------------------------------------------------------------------

def test_unparseable_body_is_malformed_request(stage):
    response = stage.client.post("/auth/signup", content="{",
                                 headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert error_code(response) == "malformed_request"


def test_non_object_json_bodies_are_malformed_request(stage):
    for payload in ([1, 2, 3], None, "text", 7):
        response = stage.client.post("/auth/login", json=payload)
        assert response.status_code == 400, payload
        assert error_code(response) == "malformed_request"


def test_fields_of_the_wrong_json_type_are_malformed_request(stage):
    response = stage.client.post("/auth/signup", json={
        "email": 5, "password": "correct horse", "display_name": "Ada"})
    assert response.status_code == 400
    assert error_code(response) == "malformed_request"


def test_valid_type_but_invalid_value_is_validation_failed(stage):
    short = stage.client.post("/auth/signup", json={
        "email": "new@example.com", "password": "123", "display_name": "New"})
    assert short.status_code == 422
    assert error_code(short) == "validation_failed"

    malformed_email = stage.client.post("/auth/signup", json={
        "email": "not-an-email", "password": "correct horse", "display_name": "New"})
    assert malformed_email.status_code == 422
    assert error_code(malformed_email) == "validation_failed"


def test_every_4xx_uses_the_shared_envelope(stage):
    cases = [
        stage.client.get("/reservations"),                                        # 401
        stage.client.get("/restaurants/r-nope"),                                  # 404
        stage.client.post("/auth/login", json=None),                              # 400
        stage.client.post("/auth/signup", json={"email": "ada@example.com",
                                               "password": "correct horse",
                                               "display_name": "Ada"}),           # 409
    ]
    for response in cases:
        assert error_code(response)


def test_unknown_route_and_method_also_use_the_envelope(stage):
    missing = stage.client.get("/definitely/not/a/route")
    assert missing.status_code == 404
    assert error_code(missing) == "not_found"

    wrong_method = stage.client.delete("/health")
    assert wrong_method.status_code == 405
    assert error_code(wrong_method)


# ---------------------------------------------------------------------------
# §6 authentication
# ---------------------------------------------------------------------------

def test_signup_returns_user_id_display_name_and_token(stage):
    response = stage.client.post("/auth/signup", json={
        "email": "Cara@Example.COM", "password": "correct horse",
        "display_name": "Cara", "experimental_field": {"ignored": True}})
    assert response.status_code == 201

    body = response.json()
    assert set(body) == {"user_id", "display_name", "token"}
    assert body["display_name"] == "Cara"


def test_duplicate_email_is_case_insensitive_and_conflicts(stage):
    first = stage.client.post("/auth/signup", json={
        "email": "dana@example.com", "password": "correct horse", "display_name": "Dana"})
    assert first.status_code == 201

    second = stage.client.post("/auth/signup", json={
        "email": "DANA@Example.com", "password": "other password", "display_name": "Dana"})
    assert second.status_code == 409
    assert error_code(second) == "email_taken"


def test_login_wrong_password_or_unknown_email_is_unauthenticated(stage):
    wrong = stage.client.post("/auth/login", json={
        "email": "ada@example.com", "password": "wrong horse"})
    assert wrong.status_code == 401
    assert error_code(wrong) == "unauthenticated"

    unknown = stage.client.post("/auth/login", json={
        "email": "nobody@example.com", "password": "correct horse"})
    assert unknown.status_code == 401
    assert error_code(unknown) == "unauthenticated"


def test_protected_endpoints_require_a_bearer_token(stage):
    anonymous = stage.client.get("/reservations")
    assert anonymous.status_code == 401
    assert error_code(anonymous) == "unauthenticated"

    garbage = stage.client.get("/reservations", headers={"Authorization": "Bearer nope"})
    assert garbage.status_code == 401

    malformed = stage.client.get("/reservations", headers={"Authorization": "nope"})
    assert malformed.status_code == 401


def test_public_endpoints_stay_open(stage, slot_local):
    stage.book("k-open", slot_local)

    assert stage.client.get("/restaurants").status_code == 200
    assert stage.client.get("/restaurants/r_anker").status_code == 200
    assert stage.availability(party_size=4).status_code == 200


def test_password_is_never_stored_in_plaintext(stage, db_file):
    import sqlite3

    with sqlite3.connect(db_file) as conn:
        rows = conn.execute("SELECT password_hash FROM users").fetchall()

    assert rows
    for (stored,) in rows:
        assert "correct horse" not in stored
        assert stored.startswith("scrypt$"), stored


def test_token_is_stored_only_as_a_hash(stage, db_file):
    import sqlite3

    with sqlite3.connect(db_file) as conn:
        stored = [row[0] for row in conn.execute("SELECT token_hash FROM auth_tokens")]

    assert stored
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in stored)
