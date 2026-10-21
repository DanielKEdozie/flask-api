from functools import wraps

from flask import Response, jsonify, make_response, request


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
            if headers:
                body.headers.extend(headers)
            return body

        if status == 204 and (body is None or body == ''):
            resp = Response(status=204)
            if headers:
                resp.headers.extend(headers)
            return resp

        resp = make_response(jsonify(body), status)
        if headers:
            resp.headers.extend(headers)
        return resp

    return wrapper


def validate_json(fn):
    """Require a valid JSON request body before invoking ``fn``."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not request.is_json:
            return jsonify({'message': 'Request must be JSON'}), 400
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({'message': 'Invalid or malformed JSON body'}), 400
        return fn(*args, **kwargs)

    return wrapper
