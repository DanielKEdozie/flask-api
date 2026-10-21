class ResourceHookMixin:
    """Lifecycle hooks around persistence.

    Hook order: ``before_save`` (raw payload) -> validate -> persist ->
    ``after_save`` (committed instance); likewise ``before_update`` /
    ``after_update`` and ``before_delete`` / ``after_delete``.
    """

    hooks = {}

    def run_hook(self, name, *args, **kwargs):
        """Run a lifecycle hook; falls back to the first arg when the hook
        is missing or returns None."""
        fallback = args[0] if args else None
        hook = (self.hooks or {}).get(name)
        if hook is None:
            return fallback
        result = hook(*args, **kwargs)
        return result if result is not None else fallback
