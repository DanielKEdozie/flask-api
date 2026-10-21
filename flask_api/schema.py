import inspect

from marshmallow import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .database import get_session


class ResourceSchemaMixin:
    """Serialization: load, dump, envelope, and error payload normalization."""

    STATUS_BY_EXCEPTION = {
        ValidationError: 400,
        SQLAlchemyError: 400,
    }

    def _session(self):
        return get_session(explicit_db=getattr(self, 'db', None))

    def load_with_schema(self, data, instance=None, partial=False):
        kwargs = {'partial': partial}
        load_params = inspect.signature(self.schema.load).parameters
        accepts_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in load_params.values()
        )
        if instance is not None and ('instance' in load_params or hasattr(self.schema, 'instance') or accepts_var_kw):
            kwargs['instance'] = instance
        if 'session' in load_params or hasattr(self.schema, 'session'):
            session = getattr(self.schema, 'session', None)
            if session is None:
                try:
                    session = self._session()
                except RuntimeError:
                    session = None
            if session is not None and 'session' in load_params:
                kwargs['session'] = session
        return self.schema.load(data, **kwargs)

    def dump_with_schema(self, instance, many=False):
        return self.schema.dump(instance, many=many)

    def envelope_response(self, data):
        key = self.envelope_key
        if not key:
            return data
        return {key: data}

    def normalize_loaded(self, loaded, instance=None):
        """Coerce ``schema.load()`` output into a SQLAlchemy model instance."""
        if isinstance(loaded, self.model):
            return loaded
        instance = instance or self.model()
        for key, value in loaded.items():
            setattr(instance, key, value)
        return instance

    def error_response(self, key, *, message=None, error=None):
        """Build an error payload, honoring a custom handler from
        ``errors`` when one is registered for the status or exception class.

        Payload handed to the handler: the exception instance for exception
        keys, otherwise the message. The handler may return a body or a
        ``(body, status)`` tuple.
        """
        if isinstance(key, type) and issubclass(key, BaseException):
            status = (
                getattr(error, 'code', None)
                or self.STATUS_BY_EXCEPTION.get(key, 400)
            )
            payload = error if error is not None else message
        else:
            status = key
            payload = message
        handler = (self.errors or {}).get(key)
        if handler is None:
            payload = message if message is not None else str(error)
            return {'message': payload}, status
        result = handler(payload)
        if isinstance(result, tuple) and len(result) == 2:
            return result
        return result, status
