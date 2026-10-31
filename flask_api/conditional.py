"""ETag and conditional-request helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from flask import request
from werkzeug.http import parse_etags


def build_etag(instance: Any, payload: Any) -> str:
    """Build a strong, representation-based ETag.

    Hashing the serialized payload means the validator changes whenever the
    response representation changes, including related records loaded eagerly.
    """
    pk_value = getattr(instance, 'id', None)
    try:
        serialized = json.dumps(
            payload, sort_keys=True, separators=(',', ':'), default=str
        )
    except (TypeError, ValueError):
        serialized = repr(payload)
    digest = hashlib.sha256(
        f'{type(instance).__name__}:{pk_value}:{serialized}'.encode('utf-8')
    ).hexdigest()
    return f'"{digest}"'


def _unquote(etag: str) -> str:
    if len(etag) >= 2 and etag.startswith('"') and etag.endswith('"'):
        return etag[1:-1]
    return etag


def if_none_match(etag: str) -> bool:
    """Weak comparison for ``If-None-Match`` (RFC 9110)."""
    header = request.headers.get('If-None-Match')
    if not header:
        return False
    tags = parse_etags(header)
    if tags.star_tag:
        return True
    return tags.contains_weak(_unquote(etag)) or tags.contains_raw(etag)


def if_match(etag: str) -> bool:
    """Strong comparison for ``If-Match`` (RFC 9110)."""
    header = request.headers.get('If-Match')
    if not header:
        return False
    tags = parse_etags(header)
    if tags.star_tag:
        return True
    return tags.contains_raw(etag)


def has_if_match() -> bool:
    return request.headers.get('If-Match') is not None
