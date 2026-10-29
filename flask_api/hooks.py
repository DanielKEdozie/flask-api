import inspect


class ResourceHookMixin:
    """Lifecycle hooks around persistence and validation.

    Hook signatures:
        - ``before_load(data, ctx)``
        - ``before_create_load(data, ctx)``
        - ``before_update_load(data, instance, ctx)``
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
        pass_kwargs = {}
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
            has_varkw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values()
            )
            param_names = [p.name for p in pos_params]

            if kwargs and not has_varargs and all(p_name in kwargs for p_name in param_names):
                call_args = [kwargs[p_name] for p_name in param_names]
            elif not has_varargs and len(pos_params) < len(args):
                call_args = args[:len(pos_params)]

            if has_varkw:
                pass_kwargs = kwargs
        except (ValueError, TypeError):
            pass

        result = hook(*call_args, **pass_kwargs)
        return result if result is not None else fallback
