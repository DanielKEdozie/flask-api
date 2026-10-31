from functools import wraps

from flask import Response, jsonify, make_response, request

from .errors import error_payload, get_request_id


def _copy_headers(response, headers):
    """Copy a mapping or iterable of ``(name, value)`` pairs onto ``response``."""
    if headers:
        items = headers.items() if hasattr(headers, 'items') else headers
        for name, value in items:
            response.headers.add(name, str(value))
    return response


def json_response(fn):
    """Normalize view return values into a JSON ``Response``.

    Supports:
        - A Werkzeug/Flask ``Response`` instance (returned as-is)
        - ``(body, status)`` or ``(body, status, headers)`` tuples
        - A dict/list body (serialized with ``jsonify`` and status 200)
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        if isinstance(result, Response):
            return result

        headers = None
        status = 200
        body = result

        if isinstance(result, tuple):
            if len(result) == 2:
                body, status = result
            elif len(result) == 3:
                body, status, headers = result
            else:
                return make_response(*result)

        if isinstance(body, Response):
            body.status_code = status
            return _copy_headers(body, headers)

        if status == 304 or (status == 204 and (body is None or body == '')):
            return _copy_headers(Response(status=status), headers)

        return _copy_headers(make_response(jsonify(body), status), headers)

    return wrapper


def validate_json(fn):
    """Require a valid JSON request body before invoking ``fn``."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not request.is_json:
            return jsonify(error_payload(
                'Request must be JSON',
                'json_required',
                request_id=get_request_id(),
            )), 400
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify(error_payload(
                'Invalid or malformed JSON body',
                'invalid_json',
                request_id=get_request_id(),
            )), 400
        return fn(*args, **kwargs)

    return wrapper
