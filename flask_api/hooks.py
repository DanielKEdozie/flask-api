import inspect

from flask import request


class HookContext:
    """Context object passed to resource lifecycle hooks (``before_save``,
    ``after_save``, ``before_update``, ``after_update``, ``before_delete``,
    ``after_delete``, ``before_load``).

    Supports both attribute access (``ctx.product``, ``ctx.session``) and
    dictionary-style access (``ctx['product']``).

    Available attributes:
        - ``ctx.item`` / ``ctx.instance``: the SQLAlchemy model instance
        - ``ctx.data`` / ``ctx.json``: the request JSON payload dictionary
        - ``ctx.loaded``: the raw output returned by ``schema.load(...)``
        - ``ctx.action``: ``'create'``, ``'update'``, or ``'delete'``
        - ``ctx.pk``: detail primary key (or ``None`` on create)
        - ``ctx.args``: query string parameters (``request.args``)
        - ``ctx.model``: the SQLAlchemy model class of the resource
        - ``ctx.schema``: the Marshmallow schema instance of the resource
        - ``ctx.resource``: the ``ApiResource`` subclass
        - ``ctx.view``: the active ``ApiResource`` instance
        - ``ctx.session``: the active SQLAlchemy session
        - ``ctx.scope``: resolved parent scoping bindings (``{binding: value}``)
        - ``ctx.route_context``: resolved route parameters
        - ``ctx.<binding>``: dynamic lookup for resolved parent bindings
          (e.g. ``ctx.product``, ``ctx.delivery_zone``, ``ctx.order``)
    """

    def __init__(self, view, action, pk=None, item=None, data=None, loaded=None):
        self.view = view
        self.action = action
        self.pk = pk
        self.item = item
        self.instance = item
        self.data = data if data is not None else {}
        self.json = self.data
        self.loaded = loaded
        self.args = request.args
        self.model = getattr(view, 'model', None)
        self.schema = getattr(view, 'schema', None)
        self.resource = view.__class__
        self.scope = getattr(view, 'scope', {})
        self.route_context = getattr(view, 'route_context', {})

    @property
    def session(self):
        view = self.__dict__.get('view')
        if view is not None and hasattr(view, '_session'):
            return view._session()
        return None

    def __getitem__(self, key):
        if hasattr(self, key):
            return getattr(self, key)
        if key in self.route_context:
            return self.route_context[key]
        if key in self.scope:
            return self.scope[key]
        raise KeyError(key)

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
        raise AttributeError(f"'HookContext' object has no attribute '{name}'")

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return (
            hasattr(self, key)
            or key in self.route_context
            or key in self.scope
        )


class ResourceHookMixin:
    """Lifecycle hooks around persistence.

    Hook execution order:
        1. ``before_load(data, ctx)`` (optional raw payload hook prior to ``schema.load``)
        2. ``schema.load(...)`` -> ``item`` (model instance)
        3. ``before_save(item, data, ctx)`` / ``before_update(item, data, ctx)``
           (runs after schema loads and populates ``item``, before ``session.commit()``)
        4. ``session.commit()``
        5. ``after_save(item, data, ctx)`` / ``after_update(item, data, ctx)``
           (runs after ``session.commit()``)
        6. ``before_delete(item, ctx)`` -> ``session.commit()`` -> ``after_delete(item, ctx)``

    Hooks can accept ``(item, data, ctx)``, ``(item, data)``, or ``(item)`` —
    ``run_hook`` inspects the callable's signature and passes the matching arguments.
    """

    hooks = {}

    def build_hook_context(self, action, pk=None, item=None, data=None, loaded=None):
        """Create a :class:`HookContext` for the current request and resource action."""
        return HookContext(
            self,
            action=action,
            pk=pk,
            item=item,
            data=data,
            loaded=loaded,
        )

    def run_hook(self, name, *args, ctx=None, **kwargs):
        """Run a lifecycle hook; falls back to the first arg when the hook
        is missing or returns ``None``.

        If ``ctx`` is provided, it is appended to the candidate positional
        arguments (e.g. ``(item, data, ctx)`` or ``(item, ctx)``) and trimmed
        automatically to match the hook callable's parameter count.
        """
        fallback = args[0] if args else None
        hook = (self.hooks or {}).get(name)
        if hook is None:
            return fallback

        candidate_args = (*args, ctx) if ctx is not None else args
        call_args = candidate_args
        call_kwargs = dict(kwargs)

        try:
            sig = inspect.signature(hook)
            pos_params = [
                p for p in sig.parameters.values()
                if p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            has_varargs = any(
                p.kind == inspect.Parameter.VAR_POSITIONAL
                for p in sig.parameters.values()
            )
            kwonly_names = {
                p.name for p in sig.parameters.values()
                if p.kind == inspect.Parameter.KEYWORD_ONLY
            }

            if ctx is not None and 'ctx' in kwonly_names and 'ctx' not in call_kwargs:
                call_kwargs['ctx'] = ctx
                candidate_args = args

            if not has_varargs:
                call_args = candidate_args[:len(pos_params)]
        except (ValueError, TypeError):
            pass

        result = hook(*call_args, **call_kwargs)
        return result if result is not None else fallback
