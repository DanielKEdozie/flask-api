import inspect

from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.exceptions import HTTPException

from .database import get_session
from .errors import STATUS_CODES, ApiError, error_payload, get_request_id


class ResourceSchemaMixin:
    """Serialization: load, dump, envelope, and error payload normalization."""

    STATUS_BY_EXCEPTION = {
        ValidationError: 400,
        IntegrityError: 409,
        SQLAlchemyError: 500,
    }

    EXCEPTION_CODES = {
        ValidationError: 'validation_error',
        IntegrityError: 'conflict',
        SQLAlchemyError: 'database_error',
    }

    def _session(self):
        return get_session(explicit_db=getattr(self, 'db', None))

    def load_with_schema(self, data, instance=None, partial=False, action=None):
        schema = self.resolve_schema(action=action, mode='load') if hasattr(self, 'resolve_schema') else self.schema
        kwargs = {'partial': partial}
        load_params = inspect.signature(schema.load).parameters
        accepts_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in load_params.values()
        )
        if instance is not None and ('instance' in load_params or hasattr(schema, 'instance') or accepts_var_kw):
            kwargs['instance'] = instance
        if 'session' in load_params or hasattr(schema, 'session'):
            session = getattr(schema, 'session', None)
            if session is None:
                try:
                    session = self._session()
                except RuntimeError:
                    session = None
            if session is not None and 'session' in load_params:
                kwargs['session'] = session
        return schema.load(data, **kwargs)

    def dump_with_schema(self, instance, many=False, action=None):
        schema = self.resolve_schema(action=action, mode='dump') if hasattr(self, 'resolve_schema') else self.schema
        return schema.dump(instance, many=many)

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

    # --- errors --------------------------------------------------------

    @staticmethod
    def _request_id():
        try:
            return get_request_id()
        except RuntimeError:
            return None

    def _status_for(self, key, error):
        if isinstance(key, int):
            return key
        if isinstance(error, HTTPException) and error.code:
            return int(error.code)
        if isinstance(key, type):
            for cls in key.__mro__:
                if cls in self.STATUS_BY_EXCEPTION:
                    return self.STATUS_BY_EXCEPTION[cls]
        return 500

    def _code_for(self, key, status):
        if isinstance(key, type):
            for cls in key.__mro__:
                if cls in self.EXCEPTION_CODES:
                    return self.EXCEPTION_CODES[cls]
        return STATUS_CODES.get(status, 'error')

    def _handler_for(self, key):
        handlers = self.errors or {}
        if isinstance(key, type):
            for cls in key.__mro__:
                if cls in handlers:
                    return handlers[cls]
        return handlers.get(key)

    def _default_message(self, key, error, status):
        if isinstance(error, ApiError):
            return error.message
        if isinstance(error, ValidationError):
            return 'Validation error'
        if isinstance(error, IntegrityError):
            return 'Record conflicts with existing data'
        if isinstance(error, SQLAlchemyError):
            return 'Database error'
        if isinstance(error, HTTPException):
            return error.description or 'Request failed'
        if error is not None and status < 500:
            return str(error)
        fallback = STATUS_CODES.get(status, 'Request failed')
        return fallback.replace('_', ' ').capitalize()

    def error_response(
        self,
        key,
        *,
        message=None,
        error=None,
        code=None,
        details=None,
        headers=None,
    ):
        """Build an error payload, honoring a custom handler from ``errors``.

        Payload handed to the handler: the exception instance for exception
        keys, otherwise the message. The handler may return a body or a
        ``(body, status)`` tuple. Default payloads keep the historical
        top-level ``message`` and add an ``error`` object with a stable code,
        validation details, request id, and optional documentation URL.
        """
        status = self._status_for(key, error)
        resolved_code = code or (getattr(error, 'code', None) if isinstance(error, ApiError) else None)
        resolved_code = resolved_code or self._code_for(key, status)
        resolved_message = message or self._default_message(key, error, status)

        if details is None and isinstance(error, ValidationError):
            details = error.messages
        if details is None and isinstance(error, ApiError):
            details = error.details

        merged_headers = dict(getattr(error, 'headers', {}) or {})
        merged_headers.update(headers or {})

        handler = self._handler_for(key)
        if handler is None:
            payload = error_payload(
                resolved_message,
                resolved_code,
                details=details,
                request_id=self._request_id(),
                documentation_url=getattr(self, 'error_docs_url', None),
            )
        else:
            payload_arg = error if (isinstance(key, type) and error is not None) else resolved_message
            result = handler(payload_arg)
            if isinstance(result, tuple) and len(result) == 2:
                payload, status = result
            else:
                payload = result
            request_id = self._request_id()
            if request_id and isinstance(payload, dict):
                payload.setdefault('request_id', request_id)

        if merged_headers:
            return payload, status, merged_headers
        return payload, status
