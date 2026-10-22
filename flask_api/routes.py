from functools import wraps
import re
from types import SimpleNamespace

from flask import request
from marshmallow import ValidationError
import sqlalchemy as sa

from .database import get_session
from .decorators import json_response

BODY_METHODS = ('POST', 'PUT', 'PATCH')


class RouteContext:
    """Context handed to ``@resource.route`` functions as ``ctx``.

    - ``ctx.parent``: ``.model``, ``.schema``, ``.resource``, ``.name`` of
      the resource the route hangs off
    - ``ctx.value``: the resolved record. With ``route_params`` it is the
      param that has no ``resolve_params`` entry (else the first one); with
      plain ``detail=True`` it is the parent record; otherwise None
    - ``ctx.<name>``: every resolved param under its ``resolve_params``
      name (or ``value_name`` for ``ctx.value``), also the resource's own
      resolved ``route_params`` bindings
    - ``ctx.params``: ``{url_param: resolved}``; ``ctx.pk`` /
      ``ctx.args`` / ``ctx.json`` / ``ctx.data`` (the payload loaded through
      the schema when ``validate=True``) / ``ctx.view``
    """

    def __init__(self, view, parent, pk):
        self.view = view
        self.parent = parent
        self.pk = pk
        self.args = request.args
        self.json = None
        self.data = None
        self.value = None
        self.params = {}
        self._names = {}

    def __getattr__(self, name):
        state = self.__dict__
        if name in state.get('_names', {}):
            return state['_names'][name]
        view = state.get('view')
        if view is not None and name in view.route_context:
            return view.route_context[name]
        raise AttributeError(name)


def _pick_schema(schemas, method, default):
    if isinstance(schemas, dict):
        upper = {str(k).upper(): v for k, v in schemas.items()}
        schema = upper.get(method) or upper.get('*') or default
    else:
        schema = schemas or default
    return schema() if isinstance(schema, type) else schema


def _lookup(spec, raw, explicit_db=None):
    """Fetch the record for a ``route_params`` spec (None when missing);
    without a model the raw url value is returned."""
    model = spec.get('model')
    if model is None:
        return raw
    field = spec.get('field', 'id')
    if not isinstance(field, str):
        field = getattr(field, 'key', None) or 'id'
    session = get_session(explicit_db=explicit_db)
    return session.scalar(
        sa.select(model).where(getattr(model, field) == raw)
    )


def register_route(resource, path, methods=('GET',), schemas=None,
                   validate=True, detail=False, route_params=None,
                   resolve_params=None, value_name=None, decorators=(),
                   docs=None, endpoint_name=None):
    """Return a decorator mounting ``fn(schema, ctx)`` as a custom route
    on ``resource`` (see ``ApiResource.route``)."""
    instance = resource.api_instance
    if instance is None or instance.app_or_bp is None:
        raise ValueError(
            'route needs a resource created through an '
            'ApiResource(app_or_bp) instance'
        )
    methods = [m.upper() for m in methods]
    app_or_bp = instance.app_or_bp
    res_name = (
        resource.resource_name
        or resource._resolve_resource_name(None)
    )
    parent_base = (resource.path or res_name).strip('/')
    prefix = resource._resolve_prefix(app_or_bp, resource.route_prefix)
    path = path.strip('/')
    resolve_params = dict(resolve_params or {})

    # detail=True without route_params is the parent record by its pk
    if detail and not route_params:
        route_params = {'pk': dict(
            model=resource.model, type=resource.pk_type,
            field=resource.pk_field,
        )}
    route_params = dict(route_params or {})

    if '<' in path:
        url = f'{prefix}/{path}'
    elif route_params:
        url_parts = []
        for param, spec in route_params.items():
            converter = resource._converter_name((spec or {}).get('type'))
            url_parts.append(f'<{converter}:{param}>' if converter else f'<{param}>')
        url = f'{prefix}/{parent_base}/{"/".join(url_parts)}/{path}'
    else:
        url = f'{prefix}/{parent_base}/{path}'

    unmapped = [p for p in route_params if p not in resolve_params]
    value_param = unmapped[0] if unmapped else next(iter(route_params), None)

    def decorator(fn):
        suffix = endpoint_name or fn.__name__
        base_ep = re.sub(r'\W+', '_', res_name if not resource.path else parent_base)
        endpoint = re.sub(r'\W+', '_', f'{base_ep}_{suffix}')

        @wraps(fn)
        @json_response
        def view(**url_kwargs):
            res = resource()
            parent = SimpleNamespace(
                model=resource.model, schema=resource.schema,
                resource=resource, name=resource.resource_name,
            )

            def handler():
                error = res._resolve_route_params()
                if error is not None:
                    return error
                ctx = RouteContext(res, parent, url_kwargs.get('pk'))
                for param, spec in route_params.items():
                    record = _lookup(
                        spec or {},
                        url_kwargs.get(param),
                        explicit_db=getattr(res, 'db', None),
                    )
                    if record is None:
                        model = (spec or {}).get('model')
                        return res.error_response(
                            404, message=f'{model.__name__} not found'
                        )
                    ctx.params[param] = record
                    name = resolve_params.get(param)
                    if param == value_param:
                        ctx.value = record
                        name = value_name or name
                    if name:
                        ctx._names[name] = record
                if value_name is None and ctx.value is not None:
                    alias = type(ctx.value).__name__.lower()
                    ctx._names.setdefault(alias, ctx.value)
                schema = _pick_schema(schemas, request.method, resource.schema)
                if validate and request.method in BODY_METHODS:
                    if not request.is_json:
                        return {'message': 'Request must be JSON'}, 400
                    ctx.json = request.get_json(silent=True) or {}
                    try:
                        ctx.data = schema.load(
                            ctx.json, partial=request.method == 'PATCH'
                        )
                    except ValidationError as err:
                        return res._validation_error(err)
                return fn(schema, ctx)

            return res._run_decorated(None, handler, extra=decorators)

        if not hasattr(resource, '_custom_routes') or resource._custom_routes is getattr(resource.__mro__[1], '_custom_routes', None):
            resource._custom_routes = list(getattr(resource, '_custom_routes', ()))
        resource._custom_routes.append({
            'path': path,
            'url': url,
            'methods': methods,
            'schemas': schemas,
            'validate': validate,
            'detail': detail,
            'route_params': route_params,
            'decorators': decorators,
            'docs': docs,
            'endpoint': endpoint,
            'fn': fn,
        })

        app_or_bp.add_url_rule(
            url, endpoint=f'{endpoint}_view', view_func=view, methods=methods
        )
        return fn

    return decorator
