import copy
from contextlib import contextmanager

import pytest


def test_signup_preserves_password_whitespace(stage):
    password = " spaced password "
    response = stage.client.post('/auth/signup', json={
        'email': 'whitespace@example.com', 'password': password,
        'display_name': 'Whitespace test',
    })
    assert response.status_code == 201
    exact = stage.client.post('/auth/login', json={
        'email': 'whitespace@example.com', 'password': password,
    })
    assert exact.status_code == 200
    trimmed = stage.client.post('/auth/login', json={
        'email': 'whitespace@example.com', 'password': password.strip(),
    })
    assert trimmed.status_code == 401


@pytest.mark.parametrize('table,field,value', [
    ('restaurants', 'slot_minutes', 0),
    ('restaurants', 'slot_minutes', -1),
    ('restaurants', 'slot_minutes', True),
    ('restaurants', 'slot_minutes', '30'),
    ('restaurants', 'slot_minutes', 2 ** 64),
    ('restaurants', 'reservation_duration_minutes', -1),
    ('restaurants', 'cancellation_cutoff_minutes', -1),
    ('restaurants', 'timezone', 'Not/AZone'),
    ('tables', 'max_seats', 0),
    ('tables', 'position', -1),
    ('opening_hours', 'weekday', 7),
    ('opening_hours', 'opens', '25:00'),
    ('opening_hours', 'closes', '00:00'),
])
def test_invalid_import_configuration_is_atomic(stage, table, field, value):
    before = stage.client.get('/_test/export').json()
    damaged = copy.deepcopy(before)
    damaged['state'][table][0][field] = value
    response = stage.client.post('/_test/import', json=damaged)
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'validation_failed'
    assert stage.client.get('/_test/export').json() == before


def test_export_uses_one_snapshot_during_a_concurrent_commit(stage, monkeypatch):
    from app import exportimport
    from app.db import transaction

    original_connection = exportimport.get_connection
    before = exportimport.export_state()
    committed = []

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            cursor = self.connection.execute(sql, *args)
            if sql.startswith('SELECT') and 'FROM tables ' in sql and not committed:
                with transaction() as writer:
                    writer.execute("UPDATE restaurants SET name = 'Changed'")
                    writer.execute("UPDATE opening_hours SET opens = '00:01'")
                committed.append(True)
            return cursor

    @contextmanager
    def interleaved_connection():
        with original_connection() as connection:
            yield Connection(connection)

    with monkeypatch.context() as patch:
        patch.setattr(exportimport, 'get_connection', interleaved_connection)
        snapshot = exportimport.export_state()
    assert committed == [True]
    assert snapshot == before
    assert exportimport.export_state() != before


def test_method_not_allowed_retains_allow_header(stage):
    response = stage.client.post('/health')
    assert response.status_code == 405
    assert 'GET' in response.headers['allow']
    assert isinstance(response.json()['error']['message'], str)
