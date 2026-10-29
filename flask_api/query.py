from flask import request
import sqlalchemy as sa
from werkzeug.exceptions import BadRequest

from .database import get_session, paginate_statement


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so user input is matched literally."""
    return (
        value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    )


class QueryContext:
    """Context object passed to ``_query`` hook callables.

    Supports both attribute access (``ctx.model``) and dictionary-style access
    (``ctx['model']``).

    Available attributes:
        - ``ctx.model``: the model class of the resource
        - ``ctx.schema``: the schema instance of the resource
        - ``ctx.resource``: the ApiResource subclass
        - ``ctx.view``: the active ApiResource instance
        - ``ctx.args``: query parameters (``request.args``)
        - ``ctx.pk``: item primary key (or None for list action)
        - ``ctx.action``: ``'list'`` or ``'item'``
        - ``ctx.scope``: resolved parent scoping bindings (``{binding: value}``)
        - ``ctx.route_context``: resolved route parameters
        - ``ctx.<binding>``: dynamic lookup for resolved parent bindings
    """

    def __init__(self, view, action, pk=None, instance=None):
        self.view = view
        self.action = action
        self.pk = pk
        self.instance = instance
        self.item = instance
        self.args = request.args
        self.model = getattr(view, 'model', None)
        if hasattr(view, 'resolve_schema'):
            self.schema = view.resolve_schema(action=action)
        else:
            self.schema = getattr(view, 'schema', None)
        self.resource = view.__class__
        self.scope = getattr(view, 'scope', {})
        self.route_context = getattr(view, 'route_context', {})

    def __getitem__(self, item):
        if hasattr(self, item):
            return getattr(self, item)
        if item in self.route_context:
            return self.route_context[item]
        if item in self.scope:
            return self.scope[item]
        raise KeyError(item)

    def __getattr__(self, name):
        if name in self.__dict__:
            return self.__dict__[name]
        route_context = self.__dict__.get('route_context', {})
        if name in route_context:
            return route_context[name]
        scope = self.__dict__.get('scope', {})
        if name in scope:
            return scope[name]
        view = self.__dict__.get('view')
        if view is not None and hasattr(view, name):
            return getattr(view, name)
        raise AttributeError(f"'QueryContext' object has no attribute '{name}'")

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, item):
        return (
            hasattr(self, item)
            or item in self.route_context
            or item in self.scope
        )


class ResourceQueryMixin:
    """SQLAlchemy 2.0+ query building: lookup, eager loading, filters, search,
    sort, pagination, and per-action query overrides.
    """

    search_fields = ()
    sort_fields = ()
    default_sort_field = ''
    sort_order = 'asc'
    filter_fields = ()
    filter_types = {}
    eager_loads = ()
    _query = {}
    scope = {}
    singleton = False

    QUERY_HOOK_ACTIONS = ('list', 'item')
    TRUTHY = ('true', '1', 'yes')

    def _session(self):
        return get_session(explicit_db=getattr(self, 'db', None))

    def _apply_scope(self, query):
        """Restrict a query to the parents resolved from ``route_params``."""
        for binding, value in self.scope.items():
            column = getattr(self.model, binding, None)
            if column is None:
                raise BadRequest(f'Invalid scope binding: {binding}')
            query = query.where(column == value)
        return query

    def _bind_scope(self, instance):
        """Attach a new record to the resolved parents."""
        for binding, value in self.scope.items():
            setattr(instance, binding, value)
        return instance

    def _get_item(self, pk):
        session = self._session()
        if self.singleton:
            query = self._apply_eager(sa.select(self.model))
            query = self._apply_scope(query)
            query = self._apply_query_hook('item', query)
            return session.scalars(query).first()
        pk_column = getattr(self.model, self.pk_field, None)
        if pk_column is None:
            raise BadRequest(f'Invalid lookup field: {self.pk_field}')
        if (
            self.pk_field == 'id'
            and 'item' not in self._query
            and not self.scope
            and not self.eager_loads
        ):
            return session.get(self.model, pk)
        query = sa.select(self.model).where(pk_column == pk)
        query = self._apply_eager(query)
        query = self._apply_scope(query)
        query = self._apply_query_hook('item', query, pk=pk)
        return session.scalar(query)

    def _apply_query_hook(self, action, query, pk=None):
        hook = (self._query or {}).get(action)
        if hook is None:
            return query
        ctx = QueryContext(self, action=action, pk=pk)
        result = hook(query, ctx)
        return query if result is None else result

    def _apply_eager(self, query):
        for entry in (self.eager_loads or ()):
            if isinstance(entry, (tuple, list)) and len(entry) == 2:
                loader, field = entry
                target = (
                    getattr(self.model, field)
                    if isinstance(field, str)
                    else field
                )
                query = query.options(loader(target))
            elif isinstance(entry, str):
                query = query.options(
                    sa.orm.selectinload(getattr(self.model, entry))
                )
            else:
                query = query.options(entry)
        return query

    def _apply_filters(self, query):
        args = request.args
        for field in self.filter_fields:
            if field not in args:
                continue
            column = getattr(self.model, field, None)
            if column is None:
                raise BadRequest(f'Invalid filter field: {field}')
            cast = (self.filter_types or {}).get(field)
            try:
                value = self._cast_filter_value(cast, args[field])
            except (TypeError, ValueError) as exc:
                raise BadRequest(
                    f'Invalid filter value for: {field}'
                ) from exc
            query = query.where(column == value)
        return query

    @classmethod
    def _cast_filter_value(cls, cast, value):
        if cast is None:
            return value
        if cast is bool:
            return str(value).lower() in cls.TRUTHY
        return cast(value)

    def _apply_search(self, query):
        search = request.args.get('search')
        if not search or not self.search_fields:
            return query
        pattern = f'%{_escape_like(search)}%'
        conditions = [
            getattr(self.model, field).ilike(pattern, escape='\\')
            for field in self.search_fields
        ]
        return query.where(sa.or_(*conditions))

    def _apply_sort(self, query):
        sort = request.args.get('sort', '') or self.default_sort_field
        if not sort:
            return query
        if self.sort_fields and sort not in self.sort_fields:
            raise BadRequest(f'Invalid sort field: {sort}')
        column = getattr(self.model, sort, None)
        if column is None:
            raise BadRequest(f'Invalid sort field: {sort}')
        order = request.args.get('sort_order', self.sort_order).lower() or 'asc'
        return query.order_by(column.desc() if order == 'desc' else column.asc())

    def _apply_pagination(self, query):
        flag = request.args.get(
            'paginate', str(self.paginate).lower()
        ).lower() in self.TRUTHY
        items_key = self.items_key or 'items'
        session = self._session()
        if not flag:
            items = session.scalars(query).all()
            return {items_key: self.dump_with_schema(items, many=True, action='list')}
        page = max(request.args.get('page', 1, type=int), 1)
        per_page = request.args.get('per_page', self.page_size, type=int)
        per_page = min(max(per_page, 1), self.max_page_size)
        pagination = paginate_statement(
            query,
            page=page,
            per_page=per_page,
            error_out=False,
            explicit_db=getattr(self, 'db', None),
        )
        return {
            items_key: self.dump_with_schema(pagination.items, many=True, action='list'),
            'meta': {
                'page': pagination.page,
                'per_page': pagination.per_page,
                'total': pagination.total,
                'pages': pagination.pages,
            },
        }
