import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from flask_api import Api, ApiResource

from .conftest import Gadget, Widget, WidgetSchema, db, widget_payload


def _create(client, **overrides):
    response = client.post('/api/widgets', json=widget_payload(**overrides))
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_put_requires_the_full_representation(client):
    widget = _create(client)

    response = client.put(f"/api/widgets/{widget['id']}", json={'name': 'Renamed'})

    assert response.status_code == 400
    body = response.get_json()
    assert body['error']['code'] == 'validation_error'
    assert set(body['error']['details']) == {'sku', 'price'}


def test_patch_still_accepts_partial_payloads(client):
    widget = _create(client)

    response = client.patch(f"/api/widgets/{widget['id']}", json={'name': 'Renamed'})

    assert response.status_code == 200
    assert response.get_json()['name'] == 'Renamed'
    assert response.get_json()['price'] == 10.0


def test_put_replaces_the_representation(client):
    widget = _create(client)

    response = client.put(
        f"/api/widgets/{widget['id']}",
        json=widget_payload(name='Full replace', price=99.5, description='updated'),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body['name'] == 'Full replace'
    assert body['price'] == 99.5
    assert body['description'] == 'updated'


def test_duplicate_unique_key_returns_409(client):
    _create(client, sku='DUP-1')

    response = client.post('/api/widgets', json=widget_payload(sku='DUP-1', name='Other'))

    assert response.status_code == 409
    body = response.get_json()
    assert body['error']['code'] == 'conflict'
    assert body['message'] == 'Record conflicts with existing data'


def test_not_found_uses_the_standard_error_contract(client):
    response = client.get('/api/widgets/99999')

    assert response.status_code == 404
    body = response.get_json()
    assert body['message'] == 'Record not found'
    assert body['error']['code'] == 'not_found'
    assert body['error']['request_id']
    assert response.headers['X-Request-ID'] == body['error']['request_id']


def test_incoming_request_id_is_echoed(client):
    response = client.get('/api/widgets/99999', headers={'X-Request-ID': 'req-abc-123'})

    assert response.headers['X-Request-ID'] == 'req-abc-123'
    assert response.get_json()['error']['request_id'] == 'req-abc-123'


def test_non_json_body_is_rejected_with_a_stable_code(client):
    response = client.post('/api/widgets', data='not json', content_type='text/plain')

    assert response.status_code == 400
    assert response.get_json()['error']['code'] == 'json_required'


def test_unpaginated_response_is_capped(client):
    for index in range(3):
        _create(client, sku=f'CAP-{index}')

    response = client.get('/api/limited-widgets?paginate=false')

    assert response.status_code == 400
    body = response.get_json()
    assert body['error']['code'] == 'unpaginated_response_too_large'
    assert body['error']['details']['max_unpaginated'] == 2

    paginated = client.get('/api/limited-widgets?paginate=true')
    assert paginated.status_code == 200
    assert paginated.get_json()['meta']['total'] == 3


def test_factory_hooks_merge_with_class_hooks():
    def before_save(item, data, ctx):
        return item

    def before_delete(item, ctx):
        return item

    class BaseWidget(ApiResource):
        model = Widget
        schema = WidgetSchema
        hooks = {'before_save': before_save}

    resource = BaseWidget.create_resource(hooks={'before_delete': before_delete})

    assert set(resource.hooks) == {'before_save', 'before_delete'}


def test_unknown_resource_option_is_rejected():
    class PlainWidget(ApiResource):
        model = Widget
        schema = WidgetSchema

    with pytest.raises(ValueError, match='Unknown resource option'):
        PlainWidget.create_resource(pagineate=False)


def test_unknown_api_option_is_rejected():
    with pytest.raises(ValueError, match='Unknown Api option'):
        Api(db=db, envelop_key='data')


def test_default_sort_order_alias_is_honoured():
    resource = ApiResource.create_resource(
        model=Widget,
        schema=WidgetSchema,
        resource_name='aliased-widgets',
        default_sort_order='desc',
    )

    assert resource.sort_order == 'desc'


def test_error_handlers_are_matched_along_the_exception_mro():
    class DatabaseAwareResource(ApiResource):
        model = Widget
        schema = WidgetSchema
        errors = {
            SQLAlchemyError: lambda exc: {
                'message': 'db error',
                'kind': type(exc).__name__,
            }
        }

    view = DatabaseAwareResource()
    exc = IntegrityError('stmt', {}, Exception('unique'))
    payload, status = view.error_response(IntegrityError, error=exc)

    assert status == 409
    assert payload['kind'] == 'IntegrityError'


def test_require_if_match_must_be_paired_with_etags():
    class ForcedResource(ApiResource):
        model = Gadget
        schema = object()
        require_if_match = True
        etag_enabled = False

    with pytest.raises(ValueError, match='requires etag_enabled'):
        ForcedResource._validate_config()
