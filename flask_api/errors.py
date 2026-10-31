"""Error primitives and request-id helpers for ``flask_api``.

Every default error response keeps the historical top-level ``message`` key so
existing clients keep working, and adds a machine-readable ``error`` object::

    {
        "message": "Record not found",
        "error": {
            "code": "not_found",
            "request_id": "4f1c...",
            "documentation_url": "https://example.com/errors#not_found"
        }
    }
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from flask import Response, g, request

REQUEST_ID_HEADER = 'X-Request-ID'
_REQUEST_ID_RE = re.compile(r'^[A-Za-z0-9._:-]{1,128}$')

STATUS_CODES = {
    400: 'bad_request',
    401: 'unauthorized',
    403: 'forbidden',
    404: 'not_found',
    405: 'method_not_allowed',
    409: 'conflict',
    412: 'precondition_failed',
    413: 'payload_too_large',
    415: 'unsupported_media_type',
    422: 'unprocessable_entity',
    428: 'precondition_required',
    429: 'rate_limited',
    500: 'internal_error',
    503: 'service_unavailable',
}


class ApiError(Exception):
    """Raise from a resource, hook, or custom route to return a structured error.

    ``_run_decorated`` converts it into the standard error payload and status.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status = int(status)
        self.code = code
        self.message = message
        self.details = details
        self.headers = dict(headers or {})


def get_request_id() -> str | None:
    """Return the request id assigned by the extension, if any."""
    return getattr(g, 'request_id', None)


def ensure_request_id() -> None:
    """Assign a request id from ``X-Request-ID`` or a fresh uuid4 hex.

    Must return ``None``: Flask uses a non-None ``before_request`` result as
    the response, which would short-circuit the view.
    """
    incoming = request.headers.get(REQUEST_ID_HEADER)
    request_id = incoming if incoming and _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
    g.request_id = request_id


def _apply_request_id_header(response: Response) -> Response:
    request_id = getattr(g, 'request_id', None)
    if request_id:
        response.headers.setdefault(REQUEST_ID_HEADER, request_id)
    return response


def install_request_id_hooks(target) -> None:
    """Attach request-id hooks to a Flask app or Blueprint exactly once.

    Blueprints cannot register request hooks after they are registered on an
    app; in that case the hooks are skipped rather than failing the mount.
    """
    if target is None or getattr(target, '_flask_api_request_id_hooks', False):
        return
    try:
        target.before_request(ensure_request_id)
        target.after_request(_apply_request_id_header)
    except (AssertionError, RuntimeError):  # setup finished (blueprint registered)
        return
    target._flask_api_request_id_hooks = True


def error_payload(
    message: str,
    code: str,
    *,
    details: Any = None,
    request_id: str | None = None,
    documentation_url: str | None = None,
) -> dict:
    """Build a default error body with a stable ``error`` object."""
    error: dict[str, Any] = {'code': code}
    if details is not None:
        error['details'] = details
    if request_id:
        error['request_id'] = request_id
    if documentation_url:
        error['documentation_url'] = documentation_url
    return {'message': message, 'error': error}
