from flask_api import IdempotencyRecord, MemoryIdempotencyStore

from .conftest import widget_payload


def test_repeated_post_replays_the_first_response(client):
    body = widget_payload(sku='IDEM-1')
    headers = {'Idempotency-Key': 'create-idem-1'}

    first = client.post('/api/widgets', json=body, headers=headers)
    second = client.post('/api/widgets', json=body, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.get_json()['id'] == second.get_json()['id']
    assert second.headers['Idempotent-Replayed'] == 'true'
    assert client.get('/api/widgets').get_json()['meta']['total'] == 1


def test_same_key_with_a_different_body_conflicts(client):
    headers = {'Idempotency-Key': 'create-idem-2'}
    first = client.post('/api/widgets', json=widget_payload(sku='IDEM-2'), headers=headers)
    assert first.status_code == 201

    second = client.post(
        '/api/widgets',
        json=widget_payload(sku='IDEM-2', name='Different'),
        headers=headers,
    )

    assert second.status_code == 409
    assert second.get_json()['error']['code'] == 'idempotency_key_reuse'


def test_invalid_key_is_rejected(client):
    response = client.post(
        '/api/widgets',
        json=widget_payload(sku='IDEM-3'),
        headers={'Idempotency-Key': 'not a valid key!'},
    )

    assert response.status_code == 400
    assert response.get_json()['error']['code'] == 'invalid_idempotency_key'


def test_failed_create_is_not_replayed(client):
    body = widget_payload(sku='IDEM-4')
    assert client.post('/api/widgets', json=body).status_code == 201
    headers = {'Idempotency-Key': 'create-idem-4'}

    first = client.post('/api/widgets', json=body, headers=headers)
    second = client.post('/api/widgets', json=body, headers=headers)

    assert first.status_code == 409
    assert second.status_code == 409
    assert 'Idempotent-Replayed' not in second.headers


def test_memory_store_transitions():
    store = MemoryIdempotencyStore()

    assert store.reserve('key', 'fingerprint', 60)[0] == 'acquired'
    assert store.reserve('key', 'fingerprint', 60)[0] == 'in_progress'

    store.complete(
        'key',
        IdempotencyRecord(
            fingerprint='fingerprint',
            status=201,
            body={'ok': True},
            state='completed',
        ),
        60,
    )
    state, record = store.reserve('key', 'fingerprint', 60)
    assert state == 'replay'
    assert record.body == {'ok': True}

    assert store.reserve('key', 'other-fingerprint', 60)[0] == 'conflict'

    store.release('key')
    assert store.reserve('key', 'fingerprint', 60)[0] == 'acquired'
