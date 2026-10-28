from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

TABLES = [
    {"id": "t_1", "label": "1", "capacity": 2},
    {"id": "t_2", "label": "2", "capacity": 4},
    {"id": "t_3", "label": "3", "capacity": 4},
]

USERS = [
    {"id": "u_ada", "email": "ada@example.com",
     "password": "correct horse", "display_name": "Ada"},
    {"id": "u_ben", "email": "ben@example.com",
     "password": "correct horse", "display_name": "Ben"},
]

R_ANKER = {
    "id": "r_anker",
    "name": "Zum Anker",
    "timezone": "Europe/Berlin",
    "slot_minutes": 30,
    "reservation_duration_minutes": 90,
    "cancellation_cutoff_minutes": 120,
    "opening_hours": [
        {"weekday": "thu", "opens": "18:00", "closes": "23:00"},
        {"weekday": "fri", "opens": "18:00", "closes": "23:30"},
        # Sunday 01:00-04:00 covers both 2026 transitions named in §9, which fall on a Sunday.
        {"weekday": "sun", "opens": "01:00", "closes": "04:00"},
    ],
    "tables": TABLES,
}

# Open all day so a cutoff test can book relative to "now" at whatever local hour
# the suite runs; §4 permits a start that has already passed.
R_LATE = {
    "id": "r_late",
    "name": "Nachteule",
    "timezone": "Europe/Berlin",
    "slot_minutes": 30,
    "reservation_duration_minutes": 90,
    "cancellation_cutoff_minutes": 120,
    "opening_hours": [{"weekday": name, "opens": "00:00", "closes": "23:59"}
                      for name in WEEKDAY_NAMES],
    "tables": [{"id": "t_9", "label": "9", "capacity": 8}],
}


def coming_weekday(target_weekday):
    """ISO date of the next `target_weekday` (0=mon), at least 30 days out."""
    day = date.today() + timedelta(days=30)
    return (day + timedelta(days=(target_weekday - day.weekday()) % 7)).isoformat()


def fixture_document(reservations=None, users=USERS):
    return {
        "users": [dict(user) for user in users],
        "restaurants": [dict(R_ANKER), dict(R_LATE)],
        "reservations": reservations or [],
    }


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    from app import db as db_module

    path = str(tmp_path / "tablekeeper.db")
    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", path)
    return path


@pytest.fixture
def raw_client(db_file):
    from app.main import app

    with TestClient(app) as client:
        yield client


class Stage:
    """A reset service plus the dated slots the contract tests book against."""

    def __init__(self, client, document):
        self.client = client
        self.document = document
        self.thu = coming_weekday(3)
        self.fri = coming_weekday(4)
        client.post("/_test/reset", json=document)
        self.tokens = {
            user["id"]: self.login(user["email"], user["password"])
            for user in document["users"]
        }

    def login(self, email, password):
        response = self.client.post(
            "/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        return response.json()["token"]

    def headers(self, user_id="u_ada", key=None):
        headers = {"Authorization": f"Bearer {self.tokens[user_id]}"}
        if key is not None:
            headers["Idempotency-Key"] = key
        return headers

    def reset(self, document=None):
        return self.client.post("/_test/reset", json=document or self.document)

    def availability(self, restaurant_id="r_anker", day=None, party_size=2, **params):
        query = {"restaurant_id": restaurant_id,
                 "date": self.thu if day is None else day,
                 "party_size": party_size}
        query.update(params)
        return self.client.get("/availability", params=query)

    def book(self, key, starts_at_local, table_id="t_2", party_size=4,
             restaurant_id="r_anker", user_id="u_ada", **extra):
        body = {"restaurant_id": restaurant_id,
                "table_id": table_id,
                "starts_at_local": starts_at_local,
                "party_size": party_size}
        body.update(extra)
        return self.client.post("/reservations", headers=self.headers(user_id, key),
                                json=body)

    def reference(self, key, starts_at_local=None, **kwargs):
        if starts_at_local is not None:
            kwargs["starts_at_local"] = starts_at_local
        response = self.book(key, **kwargs)
        assert response.status_code == 201, response.text
        return response.json()["reference"]

    def reservation(self, reference, user_id="u_ada"):
        return self.client.get(f"/reservations/{reference}", headers=self.headers(user_id))

    def cancel(self, reference, user_id="u_ada"):
        return self.client.post(f"/reservations/{reference}/cancel",
                                headers=self.headers(user_id))

    def amend(self, reference, user_id="u_ada", **body):
        return self.client.patch(f"/reservations/{reference}",
                                 headers=self.headers(user_id), json=body)

    def move(self, key, moves, user_id="u_ada"):
        return self.client.post("/reservation-moves", headers=self.headers(user_id, key),
                                json={"moves": moves})

    def local_now(self, zone="Europe/Berlin"):
        from zoneinfo import ZoneInfo

        return datetime.now(timezone.utc).astimezone(ZoneInfo(zone))

    def on_grid_ahead(self, minutes):
        """Next `slot_minutes` grid step (from 00:00) at least `minutes` from now."""
        local = self.local_now() + timedelta(minutes=minutes)
        bucket = 30
        minute = ((local.minute // bucket) + 1) * bucket
        if minute >= 60:
            local = local.replace(hour=min(local.hour + 1, 23), minute=0)
        else:
            local = local.replace(minute=minute)
        return local.strftime("%Y-%m-%dT%H:%M")


@pytest.fixture
def stage(raw_client):
    return Stage(raw_client, fixture_document())


@pytest.fixture
def slot_local(stage):
    """`<date>T19:00` on the fixture's Thursday: on-grid and inside opening hours."""
    return f"{stage.thu}T19:00"
