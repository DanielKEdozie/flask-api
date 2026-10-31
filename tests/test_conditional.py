from .conftest import widget_payload


def _create_widget(client, **overrides):
    response = client.post('/api/widgets', json=widget_payload(**overrides))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_detail_response_has_an_etag(client):
    widget = _create_widget(client)

    response = client.get(f"/api/widgets/{widget['id']}")

    assert response.status_code == 200
    assert response.headers['ETag'].startswith('"')


def test_if_none_match_returns_304_without_a_body(client):
    widget = _create_widget(client)
    etag = client.get(f"/api/widgets/{widget['id']}").headers['ETag']

    response = client.get(
        f"/api/widgets/{widget['id']}", headers={'If-None-Match': etag}
    )

    assert response.status_code == 304
    assert response.get_data() == b''
    assert response.headers['ETag'] == etag


def test_if_none_match_accepts_a_list_of_etags(client):
    widget = _create_widget(client)
    etag = client.get(f"/api/widgets/{widget['id']}").headers['ETag']

    response = client.get(
        f"/api/widgets/{widget['id']}",
        headers={'If-None-Match': f'"stale", {etag}'},
    )

    assert response.status_code == 304


def test_stale_if_match_returns_412(client):
    widget = _create_widget(client)
    original_etag = client.get(f"/api/widgets/{widget['id']}").headers['ETag']

    updated = client.put(
        f"/api/widgets/{widget['id']}",
        json=widget_payload(price=42.0),
        headers={'If-Match': original_etag},
    )
    assert updated.status_code == 200
    assert updated.headers['ETag'] != original_etag

    stale = client.put(
        f"/api/widgets/{widget['id']}",
        json=widget_payload(price=43.0),
        headers={'If-Match': original_etag},
    )
    assert stale.status_code == 412
    body = stale.get_json()
    assert body['error']['code'] == 'precondition_failed'
    assert body['error']['details']['etag'] == updated.headers['ETag']


def test_if_match_star_accepts_any_existing_representation(client):
    widget = _create_widget(client)

    response = client.put(
        f"/api/widgets/{widget['id']}",
        json=widget_payload(price=15.0),
        headers={'If-Match': '*'},
    )

    assert response.status_code == 200


def test_require_if_match_returns_428(client):
    created = client.post('/api/gadgets', json={'name': 'Gadget', 'price': 1.0})
    assert created.status_code == 201
    gadget = created.get_json()

    response = client.put(
        f"/api/gadgets/{gadget['id']}",
        json={'name': 'Gadget 2', 'price': 2.0},
    )

    assert response.status_code == 428
    assert response.get_json()['error']['code'] == 'precondition_required'


def test_require_if_match_accepts_the_current_etag(client):
    created = client.post('/api/gadgets', json={'name': 'Gadget', 'price': 1.0})
    gadget = created.get_json()
    etag = client.get(f"/api/gadgets/{gadget['id']}").headers['ETag']

    response = client.put(
        f"/api/gadgets/{gadget['id']}",
        json={'name': 'Gadget 2', 'price': 2.0},
        headers={'If-Match': etag},
    )

    assert response.status_code == 200
    assert response.get_json()['name'] == 'Gadget 2'
