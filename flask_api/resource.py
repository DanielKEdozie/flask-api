import logging
import re
from functools import partial

from flask import Flask, current_app, request
from flask.views import MethodView
import sqlalchemy as sa
from werkzeug.exceptions import HTTPException

from .crud import ResourceCRUDMixin
from .decorators import json_response, validate_json
from .errors import ApiError, install_request_id_hooks
from .factory import ResourceFactory
from .idempotency import DEFAULT_TTL, IDEMPOTENCY_HEADER
from .query import ResourceQueryMixin
from .routes import register_route

logger = logging.getLogger(__name__)


class class_or_instance_method:
    """Descriptor allowing a method to be called on either a class or an instance."""

    def __init__(self, fn):
        self.fn = fn

    def __get__(self, instance, owner):
        target = instance if instance is not None else owner
        return partial(self.fn, target)


class ApiResource(MethodView, ResourceCRUDMixin):
    """Generic SQLAlchemy 2.0+ CRUD resource (MethodView based).

    Created and mounted through ``Api(app_or_bp, db=db, **defaults)`` via
    ``api.create_resource(...)`` or ``api.create_resources(...)``.
    """

    model = None
    schema = None
    schemas = {}
    db = None
    resource_name = None
    route_prefix = None
    decorators = ()
    pk_field = 'id'
    pk_type = 'int'
    envelope_key = None
    items_key = 'items'
    paginate = True
    page_size = 10
    max_page_size = 50
    eager_loads = ()
    errors = {}
    response_code = {}
    view_overrides = {
        'list': None,
        'item': None,
        'show': None,
        'create': None,
        'update': None,
        'patch': None,
        'delete': None,
    }
    method_decorators = ()
    endpoint_names = {}
    docs = {}
    methods = None
    path = None
    route_params = {}
    resolve_params = {}
    singleton = False
    detail = True
    api_instance = None
    _custom_routes = ()

    # Unpaginated responses are capped; set to None to disable the guard.
    max_unpaginated = 1000

    # Conditional requests / optimistic concurrency
    etag_enabled = True
    require_if_match = False

    # Idempotent creates (disabled until an ``idempotency_store`` is set)
    idempotency_store = None
    idempotency_header = IDEMPOTENCY_HEADER
    idempotency_ttl = DEFAULT_TTL
    idempotency_scope = None

    # Base URL for per-error documentation links
    error_docs_url = None

    ALL_ACTIONS = ('list', 'item', 'create', 'update', 'patch', 'delete')

    DEFAULT_ENDPOINT_NAMES = {
        'list': 'list',
        'item': 'retrieve',
        'create': 'create',
        'update': 'update',
        'patch': 'patch',
        'delete': 'delete',
    }

    ACTION_ALIASES = {
        # Collection GET
        'list': 'list',
        'get_list': 'list',
        'get_collection': 'list',
        'collection': 'list',
        # Detail GET
        'item': 'item',
        'detail': 'item',
        'retrieve': 'item',
        'show': 'item',
        'get': 'item',
        'get_item': 'item',
        'get_one': 'item',
        'get_single': 'item',
        # Collection POST
        'create': 'create',
        'post': 'create',
        # Detail PUT
        'update': 'update',
        'put': 'update',
        # Detail PATCH
        'patch': 'patch',
        'patch_update': 'patch',
        'partial_update': 'patch',
        # Detail DELETE
        'delete': 'delete',
        'destroy': 'delete',
    }

    MERGED_OPTIONS = (
        'filter_types',
        'view_overrides',
        'errors',
        'response_code',
        'endpoint_names',
        'docs',
        'schemas',
        'hooks',
    )

    OPTION_ALIASES = {
        'pagination_item_key': 'items_key',
        'apply_eager': 'eager_loads',
        'default_sort_order': 'sort_order',
    }

    FACTORY_OPTIONS = (
        'model', 'schema', 'schemas', 'db', 'resource_name', 'route_prefix', 'decorators',
        'paginate', 'page_size', 'max_page_size', 'max_unpaginated', 'pk_field', 'pk_type',
        'envelope_key', 'items_key', 'hooks', 'filter_fields', 'filter_types',
        'search_fields', 'sort_fields', 'default_sort_field', 'sort_order',
        'eager_loads', 'view_overrides', 'method_decorators', 'errors',
        'response_code', '_query', 'path', 'route_params', 'resolve_params',
        'singleton', 'detail', 'endpoint_names', 'docs', 'methods',
        'etag_enabled', 'require_if_match',
        'idempotency_store', 'idempotency_header', 'idempotency_ttl', 'idempotency_scope',
        'error_docs_url',
    )

    # --- options/factory wiring --------------------------------------

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        raw_decorators = getattr(self.__class__, 'method_decorators', ())
        self.method_decorators = self.normalize_method_decorators(
            raw_decorators
        )
        self.scope = {}
        self.route_context = {}

    @classmethod
    def resolve_action_name(cls, key, *, allow_all=False) -> str:
        """Resolve any action or HTTP-verb alias (case-insensitive) to its canonical action name."""
        if not isinstance(key, str):
            raise ValueError(f'Action name must be a string, got {key!r}')
        cleaned = key.strip().lower().replace('-', '_')
        if allow_all and cleaned in ('all', '*'):
            return 'all'
        resolved = cls.ACTION_ALIASES.get(cleaned)
        if resolved is None:
            raise ValueError(f'Unknown method action: {key}')
        return resolved

    @classmethod
    def _normalize_action_dict(cls, mapping: dict) -> dict:
        if not isinstance(mapping, dict):
            return {}
        normalized = {}
        for k, v in mapping.items():
            canonical = cls.resolve_action_name(k)
            normalized[canonical] = v
        return normalized

    @classmethod
    def _normalize_schemas_dict(cls, mapping: dict) -> dict:
        """Normalize a ``schemas`` dictionary mapping action names to Schema classes, instances, or (req, resp) pairs."""
        if not isinstance(mapping, dict):
            return {}
        normalized = {}
        for key, val in mapping.items():
            if not isinstance(key, str):
                raise ValueError(f'Schema action key must be a string, got {key!r}')
            cleaned = key.strip().lower().replace('-', '_')
            if cleaned in ('all', '*', 'default'):
                canonical = 'default'
            else:
                canonical = cls.resolve_action_name(cleaned)
            normalized[canonical] = cls._normalize_schema_spec(val)
        return normalized

    @staticmethod
    def _normalize_schema_spec(val):
        def _inst(s):
            if s is None:
                return None
            return s() if isinstance(s, type) else s

        if isinstance(val, (tuple, list)) and len(val) == 2:
            return {
                'request': _inst(val[0]),
                'response': _inst(val[1]),
            }
        if isinstance(val, dict):
            req = val.get('request') or val.get('req') or val.get('load')
            resp = val.get('response') or val.get('res') or val.get('resp') or val.get('dump')
            return {
                'request': _inst(req),
                'response': _inst(resp),
            }
        return _inst(val)

    @class_or_instance_method
    def resolve_schema(self_or_cls, action=None, mode='dump'):
        """Resolve schema instance for the given action and mode ('dump' or 'load')."""
        schemas = getattr(self_or_cls, 'schemas', None) or {}
        canonical = None
        if action:
            try:
                canonical = self_or_cls.resolve_action_name(action)
            except (ValueError, AttributeError):
                canonical = str(action).strip().lower()

        spec = None
        if schemas:
            if canonical and canonical in schemas:
                spec = schemas[canonical]
            elif canonical == 'patch' and 'update' in schemas:
                spec = schemas['update']
            elif 'default' in schemas:
                spec = schemas['default']
            elif 'all' in schemas:
                spec = schemas['all']

        if spec is not None:
            if isinstance(spec, dict):
                if mode in ('load', 'request', 'req'):
                    target = spec.get('request') or spec.get('req') or spec.get('load') or spec.get('response') or spec.get('res')
                else:
                    target = spec.get('response') or spec.get('res') or spec.get('dump') or spec.get('request') or spec.get('req')
                if target is not None:
                    return target() if isinstance(target, type) else target
            return spec() if isinstance(spec, type) else spec

        fallback = getattr(self_or_cls, 'schema', None)
        return fallback() if isinstance(fallback, type) else fallback

    @classmethod
    def resolve_endpoint_names(cls) -> dict[str, str]:
        """Return the resolved ``{action: endpoint_suffix}`` mapping for this resource."""
        resolved = dict(cls.DEFAULT_ENDPOINT_NAMES)
        custom = getattr(cls, 'endpoint_names', None) or {}
        if isinstance(custom, dict):
            for k, val in custom.items():
                if not val:
                    continue
                canonical = cls.resolve_action_name(k)
                resolved[canonical] = str(val).strip()
                if canonical == 'update' and 'patch' not in custom and 'PATCH' not in custom and 'patch_update' not in custom and 'partial_update' not in custom:
                    # Keep patch distinct by default unless user explicitly aliases it
                    pass
        return resolved

    @classmethod
    def resolve_exposed_actions(cls) -> set[str]:
        """Return the set of exposed actions ('list', 'item', 'create', 'update', 'patch', 'delete').

        If ``cls.methods`` is None, defaults to all actions based on ``singleton`` and ``detail``.
        """
        if cls.methods is None:
            if cls.singleton:
                return {'item', 'create', 'update', 'patch', 'delete'}
            exposed = {'list', 'create'}
            if cls.detail:
                exposed.update({'item', 'update', 'patch', 'delete'})
            return exposed

        raw_methods = cls.methods
        if isinstance(raw_methods, str):
            raw_methods = [raw_methods]

        exposed = set()
        for m in raw_methods:
            if not isinstance(m, str):
                continue
            cleaned = m.strip().lower().replace('-', '_')
            if cleaned == 'get':
                if cls.singleton:
                    exposed.add('item')
                else:
                    exposed.add('list')
                    if cls.detail:
                        exposed.add('item')
            elif cleaned in ('head', 'options'):
                continue
            else:
                try:
                    action = cls.resolve_action_name(cleaned)
                    exposed.add(action)
                except ValueError:
                    pass
        return exposed

    @classmethod
    def _validate_config(cls):
        if cls.model is None:
            raise ValueError(f'{cls.__name__} requires a model')
        if cls.schema is None and not cls.schemas:
            raise ValueError(f'{cls.__name__} requires a schema or schemas')
        if cls.require_if_match and not cls.etag_enabled:
            raise ValueError(
                f'{cls.__name__}: require_if_match requires etag_enabled=True'
            )

    @classmethod
    def _validate_options(cls, options):
        """Reject unknown options and invalid values before a resource is built."""
        unknown = sorted(set(options) - set(cls.FACTORY_OPTIONS) - {'name'})
        if unknown:
            raise ValueError(
                f'Unknown resource option(s): {", ".join(unknown)}. '
                f'Allowed options: {", ".join(cls.FACTORY_OPTIONS)}'
            )

        def _positive_int(key):
            value = options.get(key)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise ValueError(f'{key} must be an integer >= 1')

        for key in ('page_size', 'max_page_size', 'max_unpaginated', 'idempotency_ttl'):
            _positive_int(key)

        for key in ('paginate', 'singleton', 'detail', 'etag_enabled', 'require_if_match'):
            value = options.get(key)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f'{key} must be a boolean')

        sort_order = options.get('sort_order')
        if sort_order is not None and sort_order not in ('asc', 'desc'):
            raise ValueError("sort_order must be 'asc' or 'desc'")

        if options.get('require_if_match') and options.get('etag_enabled') is False:
            raise ValueError('require_if_match requires etag_enabled=True')

        resource_name = options.get('resource_name')
        if resource_name is not None and (
            not isinstance(resource_name, str) or not resource_name.strip()
        ):
            raise ValueError('resource_name must be a non-empty string')

        for key in ('envelope_key', 'items_key', 'pk_field', 'path'):
            value = options.get(key)
            if value is not None and not isinstance(value, str):
                raise ValueError(f'{key} must be a string')

        pk_type = options.get('pk_type')
        if pk_type is not None and not isinstance(pk_type, (str, type)):
            raise ValueError('pk_type must be a converter name or a Python type')

        hooks = options.get('hooks')
        if hooks is not None:
            if not isinstance(hooks, dict):
                raise ValueError('hooks must be a dict mapping hook names to callables')
            for name, hook in hooks.items():
                if not isinstance(name, str) or not callable(hook):
                    raise ValueError(
                        f'Invalid hook {name!r}: hook names must be strings '
                        f'mapped to callables'
                    )

        errors = options.get('errors')
        if errors is not None:
            if not isinstance(errors, dict):
                raise ValueError('errors must be a dict mapping statuses or exceptions to callables')
            for key, handler in errors.items():
                valid_key = isinstance(key, int) or (
                    isinstance(key, type) and issubclass(key, BaseException)
                )
                if not valid_key:
                    raise ValueError(
                        f'errors key must be a status code or exception class, got {key!r}'
                    )
                if not callable(handler):
                    raise ValueError(f'errors handler for {key!r} must be callable')

        store = options.get('idempotency_store')
        if store is not None and not all(
            hasattr(store, method) for method in ('reserve', 'complete', 'release')
        ):
            raise ValueError(
                'idempotency_store must implement reserve(), complete() and release()'
            )

        header = options.get('idempotency_header')
        if header is not None and (
            not isinstance(header, str) or not header.strip()
        ):
            raise ValueError('idempotency_header must be a non-empty string')

        scope = options.get('idempotency_scope')
        if scope is not None and not callable(scope):
            raise ValueError('idempotency_scope must be callable')

        docs_url = options.get('error_docs_url')
        if docs_url is not None and not isinstance(docs_url, str):
            raise ValueError('error_docs_url must be a string')

    @classmethod
    def _seed_options(cls, options):
        """Map factory kwargs onto subclass attributes."""
        attrs = {'_custom_routes': []}
        schema = options.get('schema')
        if schema is not None:
            attrs['schema'] = schema() if isinstance(schema, type) else schema
        for key in cls.MERGED_OPTIONS:
            if options.get(key):
                base_val = dict(getattr(cls, key) or {})
                incoming = dict(options[key])
                if key in ('view_overrides', 'response_code', 'endpoint_names'):
                    incoming = cls._normalize_action_dict(incoming)
                elif key == 'schemas':
                    incoming = cls._normalize_schemas_dict(incoming)
                attrs[key] = {**base_val, **incoming}
        if options.get('_query'):
            attrs['_query'] = cls._normalize_query_hooks(options['_query'])
        if options.get('method_decorators') is not None:
            attrs['method_decorators'] = cls.normalize_method_decorators(
                options['method_decorators']
            )
        special = ('schema', '_query', 'method_decorators') + cls.MERGED_OPTIONS
        for key in cls.FACTORY_OPTIONS:
            if key not in special and options.get(key) is not None:
                attrs[key] = options[key]

        if attrs.get('schema') is None and attrs.get('schemas'):
            norm_schemas = attrs['schemas']
            first_spec = (
                norm_schemas.get('default')
                or norm_schemas.get('all')
                or norm_schemas.get('item')
                or norm_schemas.get('list')
                or (next(iter(norm_schemas.values())) if norm_schemas else None)
            )
            if isinstance(first_spec, dict):
                first_spec = first_spec.get('response') or first_spec.get('request')
            if first_spec:
                attrs['schema'] = first_spec() if isinstance(first_spec, type) else first_spec

        return attrs

    @classmethod
    def _normalize_query_hooks(cls, mapping):
        """Validate ``_query`` into ``{action: callable(query, ctx)}`` for
        the list/item actions."""
        if not isinstance(mapping, dict):
            raise ValueError(
                '_query must be an action -> callable(query, ctx) mapping'
            )
        normalized = {}
        for key, hook in mapping.items():
            action = cls.resolve_action_name(key)
            if action not in ResourceQueryMixin.QUERY_HOOK_ACTIONS:
                raise ValueError(f'Unknown _query action: {key}')
            if not callable(hook):
                raise ValueError(f'_query for {key} must be a callable')
            normalized[action] = hook
        return normalized

    @classmethod
    def create_resource(cls, **options):
        """Build a configured view class; no routes are mounted.

        ``pagination_item_key``, ``apply_eager`` and ``default_sort_order``
        are accepted as aliases for ``items_key``, ``eager_loads`` and
        ``sort_order``. Unknown options raise ``ValueError`` instead of being
        silently ignored.
        """
        for alias, target in cls.OPTION_ALIASES.items():
            if alias in options:
                value = options.pop(alias)
                if value is not None:
                    options.setdefault(target, value)
        cls._validate_options(options)
        return ResourceFactory.create(cls, **options)

    @classmethod
    def register(cls, app_or_bp, **mount_options):
        """Mount list + detail routes on an app or blueprint."""
        return ResourceFactory.register(cls, app_or_bp, **mount_options)

    @classmethod
    def _resolve_resource_name(cls, resource_name, model=None):
        if resource_name:
            return resource_name
        model = model or cls.model
        if model is not None and getattr(model, '__tablename__', None):
            return model.__tablename__
        raise ValueError('resource_name or model is required')

    @classmethod
    def _resolve_prefix(cls, app_or_bp, route_prefix):
        prefix = route_prefix if route_prefix is not None else cls.route_prefix
        if prefix is None:
            if isinstance(app_or_bp, Flask):
                prefix = app_or_bp.config.get('API_ROUTE_PREFIX', '/api')
            else:
                prefix = ''
        stripped = prefix.strip('/')
        return f'/{stripped}' if stripped else ''

    @staticmethod
    def _converter_name(type_):
        """Python type (or converter name) -> werkzeug url converter."""
        if type_ is None or isinstance(type_, str):
            return type_
        converters = {int: 'int', str: 'string', float: 'float'}
        if type_ not in converters:
            raise ValueError(f'Unsupported route param type: {type_!r}')
        return converters[type_]

    @classmethod
    def create_subresource(cls, path, resource_name=None, detail=True,
                           route_params=None, resolve_params=None,
                           singleton=False, **options):
        """Create and mount a resource nested under this one."""
        route_params = dict(route_params or {})
        if '<' not in path:
            if not route_params:
                raise ValueError(
                    'create_subresource needs route_params to build the url'
                )
            param, spec = next(iter(route_params.items()))
            converter = cls._converter_name((spec or {}).get('type'))
            part = f'<{converter}:{param}>' if converter else f'<{param}>'
            parent = (
                cls.path or cls.resource_name or cls._resolve_resource_name(None)
            ).strip('/')
            path = f'{parent}/{part}/{path.strip("/")}'
        if options.get('method_decorators') is None and cls.method_decorators:
            options['method_decorators'] = cls.method_decorators
        if options.get('route_prefix') is None and cls.route_prefix is not None:
            options['route_prefix'] = cls.route_prefix
        if options.get('db') is None and cls.db is not None:
            options['db'] = cls.db
        options.update(
            path=path.strip('/'),
            detail=detail,
            route_params=route_params,
            resolve_params=dict(resolve_params or {}),
            singleton=singleton,
        )
        if resource_name:
            options['resource_name'] = resource_name
        if cls.api_instance is None:
            raise ValueError(
                'create_subresource needs a resource created through an '
                'Api(app_or_bp) instance'
            )
        return cls.api_instance.create_resource(**options)

    @classmethod
    def route(cls, path, methods=('GET',), schemas=None, validate=True,
              detail=False, route_params=None, resolve_params=None,
              value_name=None, decorators=(), docs=None, endpoint_name=None):
        """Decorator adding a custom endpoint to this resource."""
        return register_route(
            cls, path, methods=methods, schemas=schemas, validate=validate,
            detail=detail, route_params=route_params,
            resolve_params=resolve_params, value_name=value_name,
            decorators=decorators, docs=docs, endpoint_name=endpoint_name,
        )

    @classmethod
    def _mount(cls, app_or_bp, resource_name=None, route_prefix=None,
               list_methods=None, detail_methods=None,
               detail_pk_converter=None):
        """Register list + detail routes on an app or blueprint with ``{resource_name}_{endpoint_name}`` endpoints."""
        cls._validate_config()
        install_request_id_hooks(app_or_bp)
        name = resource_name or cls.resource_name or cls._resolve_resource_name(None)
        prefix = cls._resolve_prefix(app_or_bp, route_prefix)
        base = (cls.path or name).strip('/')
        base_endpoint = re.sub(r'\W+', '_', name if resource_name or cls.resource_name else base)

        ep_names = cls.resolve_endpoint_names()
        cls.endpoints = {}

        def _register_action_rules(url_rule: str, method_to_action: list[tuple[str, str]]):
            grouped: dict[str, list[str]] = {}
            for http_method, act in method_to_action:
                suffix = ep_names.get(act, act)
                ep = f'{base_endpoint}_{suffix}' if suffix else base_endpoint
                cls.endpoints[act] = ep
                grouped.setdefault(ep, []).append(http_method)

            last_view = None
            for ep, methods_for_ep in grouped.items():
                v_func = cls.as_view(ep)
                app_or_bp.add_url_rule(
                    url_rule,
                    endpoint=ep,
                    view_func=v_func,
                    methods=methods_for_ep,
                )
                last_view = v_func
            return last_view

        collection_url = f'{prefix}/{base}'
        exposed = cls.resolve_exposed_actions()

        if cls.singleton:
            allowed = set(
                m.upper()
                for m in (
                    list_methods
                    or detail_methods
                    or ('GET', 'POST', 'PUT', 'PATCH', 'DELETE')
                )
            )
            pairs = [
                (m, a)
                for m, a in (
                    ('GET', 'item'),
                    ('POST', 'create'),
                    ('PUT', 'update'),
                    ('PATCH', 'patch'),
                    ('DELETE', 'delete'),
                )
                if m in allowed and a in exposed
            ]
            if not pairs:
                return None
            return _register_action_rules(collection_url, pairs)

        if list_methods is None:
            list_methods = ('GET', 'POST')
        if detail_methods is None:
            detail_methods = ('GET', 'PUT', 'PATCH', 'DELETE') if cls.detail else ()

        view = None
        if list_methods:
            allowed_list = {m.upper() for m in list_methods}
            list_pairs = [
                (m, a)
                for m, a in (('GET', 'list'), ('POST', 'create'))
                if m in allowed_list and a in exposed
            ]
            if list_pairs:
                view = _register_action_rules(collection_url, list_pairs)

        if detail_methods:
            converter = detail_pk_converter or cls.pk_type
            pk_part = f'<{converter}:pk>' if converter else '<pk>'
            detail_url = f'{prefix}/{base}/{pk_part}'
            allowed_detail = {m.upper() for m in detail_methods}
            detail_pairs = [
                (m, a)
                for m, a in (
                    ('GET', 'item'),
                    ('PUT', 'update'),
                    ('PATCH', 'patch'),
                    ('DELETE', 'delete'),
                )
                if m in allowed_detail and a in exposed
            ]
            if detail_pairs:
                detail_view = _register_action_rules(detail_url, detail_pairs)
                if view is None:
                    view = detail_view

        return view

    def _resolve_route_params(self):
        """Look up each ``route_params`` entry; returns an error response
        (404) when a parent record is missing, otherwise None."""
        view_args = request.view_args or {}
        self.scope = {}
        self.route_context = {}
        session = None
        for name, spec in self.route_params.items():
            value = view_args.get(name)
            model = (spec or {}).get('model')
            if model is not None:
                if session is None:
                    session = self._session()
                field = spec.get('field', 'id')
                if not isinstance(field, str):
                    field = getattr(field, 'key', None) or 'id'
                value = session.scalar(
                    sa.select(model).where(getattr(model, field) == value)
                )
                if value is None:
                    return self.error_response(
                        404, message=f'{model.__name__} not found'
                    )
            binding = self.resolve_params.get(name)
            self.route_context[binding or name] = value
            if binding:
                self.scope[binding] = value
        return None

    # --- dispatch ------------------------------------------------------

    def _handlers(self):
        return {
            'list': lambda pk: self.handle_list(),
            'item': self.handle_detail,
            'create': lambda pk: self.handle_create(),
            'update': self.handle_update,
            'patch': self.handle_patch,
            'delete': self.handle_delete,
        }

    def dispatch(self, action, pk, override):
        """Resolve and invoke the handler or override for an action,
        applying shared + per-action ``method_decorators`` first."""
        exposed = self.resolve_exposed_actions()
        if action not in exposed:
            return self.error_response(405, message=f'Method not allowed for action: {action}')
        if override:
            def handler():
                error = self._resolve_route_params()
                if error is not None:
                    return error
                return override(self, pk)
        else:
            action_handler = self._handlers()[action]

            def handler():
                error = self._resolve_route_params()
                if error is not None:
                    return error
                return action_handler(pk)

        return self._run_decorated(action, handler)

    def _run_decorated(self, action, handler, extra=()):
        """Wrap ``handler`` in the shared + per-action ``method_decorators``
        (and ``extra``), run it, and turn unexpected errors into a 500."""
        decorators = list(self.method_decorators.get('all', ())) + \
            list(self.method_decorators.get(action, ())) + list(extra)
        for decorator in reversed(decorators):
            if hasattr(decorator, '__self__') and decorator.__self__ is self:
                dec = decorator.__func__
            else:
                dec = decorator
            handler = dec(handler)
        try:
            return handler()
        except ApiError as exc:
            return self.error_response(
                exc.status,
                message=exc.message,
                code=exc.code,
                details=exc.details,
                headers=exc.headers,
            )
        except HTTPException as exc:
            return self.error_response(
                exc.code or 400, error=exc, message=exc.description
            )
        except Exception as exc:
            if current_app.testing or current_app.debug:
                raise
            logger.exception('Unhandled error in %s %s', self.__class__.__name__, action)
            return self.error_response(
                500, error=exc, message='Internal server error'
            )

    @classmethod
    def normalize_method_decorators(cls, source):
        """Normalize ``method_decorators`` into per-action lists.

        If ``update`` (or ``PUT``) decorators are specified and ``patch`` is not
        explicitly specified, ``patch`` inherits the ``update`` decorators.
        """
        normalized = {action: [] for action in cls.ALL_ACTIONS}
        normalized['all'] = []
        explicit_patch = False

        def add(action_key, decorators):
            nonlocal explicit_patch
            keys = (action_key,) if isinstance(action_key, str) else tuple(action_key)
            if not isinstance(decorators, (list, tuple, set)):
                decorators = [decorators] if callable(decorators) else []
            if not all(callable(d) for d in decorators):
                raise ValueError(
                    f'method_decorators for {action_key} must be callables'
                )
            for key in keys:
                action = cls.resolve_action_name(key, allow_all=True)
                if action == 'patch':
                    explicit_patch = True
                normalized[action].extend(decorators)

        entries = source if isinstance(source, (list, tuple, set)) else [source]
        for entry in entries:
            if callable(entry):
                add('all', [entry])
            elif isinstance(entry, dict):
                # Check if entry is already a normalized dict with all ALL_ACTIONS
                if set(entry.keys()) >= set(cls.ALL_ACTIONS) and 'all' in entry:
                    for k, v in entry.items():
                        normalized[k] = list(v)
                    return normalized
                for action_key, decorators in entry.items():
                    add(action_key, decorators)
            elif entry is not None:
                raise ValueError(
                    'method_decorators must be a decorator, an action mapping '
                    'or a collection of both'
                )

        if not explicit_patch and normalized['update']:
            normalized['patch'] = list(normalized['update'])

        return normalized

    # --- HTTP verbs ----------------------------------------------------

    def _override(self, *actions):
        for action in actions:
            override = self.view_overrides.get(action)
            if override:
                return override
        return None

    @json_response
    def get(self, pk=None, **kwargs):
        if pk is not None or self.singleton:
            return self.dispatch('item', pk, self._override('item', 'show'))
        return self.dispatch('list', None, self._override('list'))

    @validate_json
    @json_response
    def post(self, **kwargs):
        return self.dispatch('create', None, self._override('create'))

    @validate_json
    @json_response
    def put(self, pk=None, **kwargs):
        return self.dispatch('update', pk, self._override('update'))

    @validate_json
    @json_response
    def patch(self, pk=None, **kwargs):
        return self.dispatch('patch', pk, self._override('patch', 'update'))

    @json_response
    def delete(self, pk=None, **kwargs):
        return self.dispatch('delete', pk, self._override('delete'))
