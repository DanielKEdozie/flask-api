import inspect


class ResourceHookMixin:
    """Lifecycle hooks around persistence and validation.

    Hook signatures:
        - ``before_load(data, ctx)``
        - ``before_create_load(data, ctx)``
        - ``before_update_load(data, ctx)``
        - ``before_validate(data, ctx)``
        - ``before_save(item, data, ctx)``
        - ``after_save(item, ctx)``
        - ``before_update(item, data, ctx)``
        - ``after_update(item, ctx)``
        - ``before_delete(item, ctx)``
        - ``after_delete(item, ctx)``
    """

    hooks = {}

    def run_hook(self, name, *args, **kwargs):
        """Run a lifecycle hook; falls back to the first arg when the hook
        is missing or returns None."""
        fallback = args[0] if args else None
        hook = (self.hooks or {}).get(name)
        if hook is None:
            return fallback

        call_args = args
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
            if not has_varargs and len(pos_params) < len(args):
                call_args = args[:len(pos_params)]
        except (ValueError, TypeError):
            pass

        result = hook(*call_args, **kwargs)
        return result if result is not None else fallback
