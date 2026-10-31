"""Idempotency-key support.

The resource layer calls a pluggable :class:`IdempotencyStore` to reserve a key,
run the create operation once, and replay the stored response for retries.

Only ``POST`` create actions use this. A key is scoped to the authenticated
identity when one is available, and the request fingerprint (method, path,
query string, and raw body) prevents a key from being reused with a different
payload.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

IDEMPOTENCY_HEADER = 'Idempotency-Key'
REPLAY_HEADER = 'Idempotent-Replayed'
DEFAULT_TTL = 86400

_ACQUIRED = 'acquired'
_REPLAY = 'replay'
_CONFLICT = 'conflict'
_IN_PROGRESS = 'in_progress'

# Headers that are safe and useful to replay. Cookies and body framing are
# deliberately excluded.
_REPLAYABLE_HEADERS = {'etag', 'location', 'cache-control'}


@dataclass
class IdempotencyRecord:
    fingerprint: str
    status: int | None = None
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    state: str = 'in_progress'
    created_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(
            {
                'fingerprint': self.fingerprint,
                'status': self.status,
                'body': self.body,
                'headers': self.headers,
                'state': self.state,
                'created_at': self.created_at,
            },
            default=str,
            separators=(',', ':'),
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> 'IdempotencyRecord':
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8')
        data = json.loads(raw)
        return cls(
            fingerprint=data.get('fingerprint', ''),
            status=data.get('status'),
            body=data.get('body'),
            headers=dict(data.get('headers') or {}),
            state=data.get('state', 'in_progress'),
            created_at=float(data.get('created_at') or time.time()),
        )


class IdempotencyStore(Protocol):
    """Storage contract used by ``ApiResource`` create handling."""

    def reserve(self, key: str, fingerprint: str, ttl: int) -> tuple[str, IdempotencyRecord | None]:
        """Atomically claim ``key``.

        Returns ``(state, record)`` where state is one of ``acquired``,
        ``replay``, ``conflict``, or ``in_progress``.
        """

    def complete(self, key: str, record: IdempotencyRecord, ttl: int) -> None:
        """Persist a completed response for replay."""

    def release(self, key: str) -> None:
        """Release a reservation after a non-cacheable outcome."""


def request_fingerprint(
    *,
    method: str,
    path: str,
    query_string: str,
    body: bytes,
    identity: str = '',
) -> str:
    digest = hashlib.sha256()
    for part in (identity, method.upper(), path, query_string):
        digest.update(part.encode('utf-8'))
        digest.update(b'\x00')
    digest.update(body or b'')
    return digest.hexdigest()


def replayable_headers(headers: dict[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {
        str(name): str(value)
        for name, value in headers.items()
        if str(name).lower() in _REPLAYABLE_HEADERS
    }


class MemoryIdempotencyStore:
    """Thread-safe in-process store.

    Suitable for tests and single-process deployments only. Use
    :class:`RedisIdempotencyStore` when more than one worker can serve requests.
    """

    def __init__(self, max_entries: int = 1024) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        self.max_entries = max_entries

    def _purge(self, key: str) -> None:
        entry = self._entries.get(key)
        if entry is not None and entry['expires_at'] <= time.time():
            del self._entries[key]

    def _purge_expired(self) -> None:
        now = time.time()
        for key in [k for k, v in self._entries.items() if v['expires_at'] <= now]:
            del self._entries[key]

    def reserve(self, key: str, fingerprint: str, ttl: int) -> tuple[str, IdempotencyRecord | None]:
        with self._lock:
            self._purge(key)
            if len(self._entries) >= self.max_entries:
                self._purge_expired()
            entry = self._entries.get(key)
            if entry is None:
                self._entries[key] = {
                    'fingerprint': fingerprint,
                    'record': IdempotencyRecord(fingerprint=fingerprint, state='in_progress'),
                    'expires_at': time.time() + ttl,
                }
                return _ACQUIRED, None
            if entry['fingerprint'] != fingerprint:
                return _CONFLICT, entry['record']
            record = entry['record']
            if record.state == 'completed':
                return _REPLAY, record
            return _IN_PROGRESS, record

    def complete(self, key: str, record: IdempotencyRecord, ttl: int) -> None:
        with self._lock:
            self._entries[key] = {
                'fingerprint': record.fingerprint,
                'record': record,
                'expires_at': time.time() + ttl,
            }

    def release(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)


class RedisIdempotencyStore:
    """Redis-backed store using ``SET NX EX`` for atomic reservations.

    Requires the optional ``redis`` extra::

        pip install flask-api[redis]
    """

    def __init__(self, client=None, url: str | None = None, *, prefix: str = 'flask-api:idem:', **redis_kwargs):
        if client is None:
            try:
                import redis
            except ImportError as exc:  # pragma: no cover - depends on optional extra
                raise RuntimeError(
                    'RedisIdempotencyStore requires the redis package: pip install flask-api[redis]'
                ) from exc
            client = redis.Redis.from_url(url or 'redis://localhost:6379/0', **redis_kwargs)
        self.client = client
        self.prefix = prefix

    def _key(self, key: str) -> str:
        return f'{self.prefix}{key}'

    def reserve(self, key: str, fingerprint: str, ttl: int) -> tuple[str, IdempotencyRecord | None]:
        storage_key = self._key(key)
        pending = IdempotencyRecord(fingerprint=fingerprint, state='in_progress').to_json()
        if self.client.set(storage_key, pending, nx=True, ex=ttl):
            return _ACQUIRED, None

        raw = self.client.get(storage_key)
        if raw is None:
            # The key expired between SET NX and GET; try once more.
            if self.client.set(storage_key, pending, nx=True, ex=ttl):
                return _ACQUIRED, None
            raw = self.client.get(storage_key)
            if raw is None:
                return _CONFLICT, None

        record = IdempotencyRecord.from_json(raw)
        if record.fingerprint != fingerprint:
            return _CONFLICT, record
        if record.state == 'completed':
            return _REPLAY, record
        return _IN_PROGRESS, record

    def complete(self, key: str, record: IdempotencyRecord, ttl: int) -> None:
        self.client.set(self._key(key), record.to_json(), ex=ttl)

    def release(self, key: str) -> None:
        self.client.delete(self._key(key))
