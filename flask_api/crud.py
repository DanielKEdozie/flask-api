import logging
import re

from flask import Response, g, request
from marshmallow import ValidationError
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .conditional import build_etag, has_if_match, if_match, if_none_match
from .errors import ApiError
from .hooks import ResourceHookMixin
from .idempotency import (
    DEFAULT_TTL,
    IDEMPOTENCY_HEADER,
    REPLAY_HEADER,
    IdempotencyRecord,
    replayable_headers,
    request_fingerprint,
)
from .query import QueryContext, ResourceQueryMixin
from .schema import ResourceSchemaMixin

logger = logging.getLogger(__name__)

_IDEMPOTENCY_KEY_RE = re.compile(r'^[A-Za-z0-9._:-]{1,255}$')


class ResourceCRUDMixin(ResourceSchemaMixin, ResourceQueryMixin, ResourceHookMixin):
    """Default list/create/detail/update/delete handlers. Each handler is
    overridable per subclass; HTTP verbs dispatch here — names are
    ``handle_*`` prefixed so they never shadow the verb methods themselves.
    """

    NOT_FOUND = 'Record not found'

    # Conditional requests / optimistic concurrency
    etag_enabled = True
    require_if_match = False

    # Idempotent creates
    idempotency_store = None
    idempotency_header = IDEMPOTENCY_HEADER
    idempotency_ttl = DEFAULT_TTL
    idempotency_scope = None

    def _not_found(self):
        return self.error_response(404, message=self.NOT_FOUND)

    def _validation_error(self, err):
        return self.error_response(
            ValidationError, error=err, message='Validation error'
        )

    def _commit(self, failure_message, *, add=None, delete=None):
        """Persist pending changes; returns an error response or None."""
        session = self._session()
        try:
            if add is not None:
                session.add(add)
            if delete is not None:
                session.delete(delete)
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            if isinstance(exc, IntegrityError):
                failure_message = 'Record conflicts with existing data'
            return self.error_response(
                type(exc), error=exc, message=failure_message
            )
        return None

    # --- conditional requests ------------------------------------------

    def _etag_for(self, instance):
        """Build the resource's ETag from its canonical ``item`` representation.

        Using one representation everywhere keeps the ETag returned by writes
        valid for a later ``If-Match``/``If-None-Match`` on the detail route,
        even when action-specific schemas differ.
        """
        if not self.etag_enabled or instance is None:
            return None
        try:
            payload = self.envelope_response(
                self.dump_with_schema(instance, action='item')
            )
            return build_etag(instance, payload)
        except Exception:  # pragma: no cover - defensive: never break a response
            logger.debug('Could not build ETag', exc_info=True)
            return None

    def _check_preconditions(self, item):
        """Enforce ``If-Match`` for updates/deletes.

        Raises ``428`` when ``require_if_match`` is enabled and the header is
        missing, ``412`` when the supplied ETag no longer matches.
        """
        if not self.etag_enabled:
            return
        etag = self._etag_for(item)
        if not has_if_match():
            if self.require_if_match:
                raise ApiError(
                    428,
                    'precondition_required',
                    'This operation requires an If-Match header with the '
                    'current ETag.',
                )
            return
        if not etag or not if_match(etag):
            raise ApiError(
                412,
                'precondition_failed',
                'The resource has changed since the supplied ETag was issued.',
                details={'etag': etag},
            )

    # --- idempotent creates --------------------------------------------

    def _idempotency_key(self):
        if getattr(self, 'idempotency_store', None) is None:
            return None
        header = getattr(self, 'idempotency_header', None) or IDEMPOTENCY_HEADER
        key = (request.headers.get(header) or '').strip()
        if not key:
            return None
        if not _IDEMPOTENCY_KEY_RE.match(key):
            raise ApiError(
                400,
                'invalid_idempotency_key',
                f'{header} must be 1-255 characters from [A-Za-z0-9._:-].',
            )
        return key

    def _idempotency_identity(self):
        scope = getattr(self, 'idempotency_scope', None)
        if callable(scope):
            value = scope()
            if value:
                return f'custom:{value}'

        user = getattr(g, 'current_user', None) or getattr(g, 'user', None)
        if user is None:
            return ''
        if isinstance(user, dict):
            value = user.get('id') or user.get('email')
        else:
            value = getattr(user, 'id', None) or getattr(user, 'email', None)
        return f'user:{value}' if value else ''

    @staticmethod
    def _status_code(status):
        try:
            return int(status)
        except (TypeError, ValueError):
            try:
                return int(str(status).split()[0])
            except (TypeError, ValueError):
                return None

    @staticmethod
    def _result_parts(result):
        """Return ``(body, status, headers)`` for any handler return value."""
        if isinstance(result, Response):
            return result.get_json(silent=True), result.status_code, dict(result.headers)
        if isinstance(result, tuple):
            if len(result) == 2:
                return result[0], result[1], {}
            if len(result) == 3:
                return result[0], result[1], dict(result[2] or {})
        return result, 200, {}

    def _with_idempotency(self, handler):
        """Run ``handler`` once per ``Idempotency-Key`` and replay its 2xx result."""
        store = getattr(self, 'idempotency_store', None)
        if store is None:
            return handler()
        key = self._idempotency_key()
        if key is None:
            return handler()

        ttl = int(getattr(self, 'idempotency_ttl', DEFAULT_TTL) or DEFAULT_TTL)
        identity = self._idempotency_identity()
        storage_key = f'{identity}:{key}' if identity else key
        fingerprint = request_fingerprint(
            method=request.method,
            path=request.path,
            query_string=request.query_string.decode('utf-8', 'replace'),
            body=request.get_data(cache=True),
            identity=identity,
        )

        state, record = store.reserve(storage_key, fingerprint, ttl)
        if state == 'replay' and record is not None:
            headers = dict(record.headers or {})
            headers[REPLAY_HEADER] = 'true'
            return record.body, record.status or 200, headers
        if state == 'conflict':
            raise ApiError(
                409,
                'idempotency_key_reuse',
                'This Idempotency-Key was already used with a different request.',
            )
        if state == 'in_progress':
            raise ApiError(
                409,
                'idempotency_in_progress',
                'A request with this Idempotency-Key is still being processed.',
                headers={'Retry-After': '1'},
            )

        try:
            result = handler()
        except Exception:
            store.release(storage_key)
            raise

        body, status, headers = self._result_parts(result)
        status_code = self._status_code(status)
        cacheable = (
            status_code is not None
            and 200 <= status_code < 300
            and (body is not None or status_code == 204)
        )
        if cacheable:
            store.complete(
                storage_key,
                IdempotencyRecord(
                    fingerprint=fingerprint,
                    status=status_code,
                    body=body,
                    headers=replayable_headers(headers),
                    state='completed',
                ),
                ttl,
            )
        else:
            store.release(storage_key)
        return result

    def _run_load_hook(self, action, data, ctx):
        hooks = self.hooks or {}
        # 1. Action-specific hook takes priority
        action_candidates = [f'before_{action}_load', f'before_{action}_validate']
        if action == 'patch':
            action_candidates.extend(['before_update_load', 'before_update_validate'])

        for cand in action_candidates:
            if cand in hooks:
                return self.run_hook(cand, data, ctx)

        # 2. Fall back to general before_load / before_validate
        for cand in ('before_load', 'before_validate'):
            if cand in hooks:
                return self.run_hook(cand, data, ctx)

        return data

    # --- handlers -------------------------------------------------------

    def handle_list(self):
        query = sa.select(self.model)
        query = self._apply_eager(query)
        query = self._apply_scope(query)
        query = self._apply_query_hook('list', query)
        query = self._apply_filters(query)
        query = self._apply_search(query)
        query = self._apply_sort(query)
        return (
            self._apply_pagination(query),
            self.response_code.get('list', 200),
        )

    def handle_detail(self, pk):
        item = self._get_item(pk)
        if item is None:
            return self._not_found()
        payload = self.envelope_response(
            self.dump_with_schema(item, action='item')
        )
        etag = self._etag_for(item)
        if etag and if_none_match(etag):
            return Response(status=304, headers={'ETag': etag})
        status = self.response_code.get('item', 200)
        if etag:
            return payload, status, {'ETag': etag}
        return payload, status

    def handle_create(self):
        return self._with_idempotency(self._create)

    def _create(self):
        if self.singleton and self._get_item(None) is not None:
            return self.error_response(409, message='Record already exists')
        data = request.get_json(silent=True) or {}
        ctx = QueryContext(self, action='create')
        try:
            data = self._run_load_hook('create', data, ctx)
            loaded = self.load_with_schema(data, action='create')
        except ValidationError as err:
            return self._validation_error(err)
        instance = self._bind_scope(self.normalize_loaded(loaded))
        ctx.instance = instance
        ctx.item = instance
        hook_res = self.run_hook('before_save', instance, data, ctx)
        if isinstance(hook_res, dict):
            for k, v in hook_res.items():
                setattr(instance, k, v)
        elif hook_res is not None:
            instance = hook_res
            ctx.instance = instance
            ctx.item = instance
        error = self._commit('Could not create record', add=instance)
        if error:
            return error
        instance = self.run_hook('after_save', instance, ctx)
        payload = self.envelope_response(
            self.dump_with_schema(instance, action='create')
        )
        status = self.response_code.get('create', 201)
        etag = self._etag_for(instance)
        if etag:
            return payload, status, {'ETag': etag}
        return payload, status

    def handle_update(self, pk, *, action='update'):
        item = self._get_item(pk)
        if item is None:
            return self._not_found()
        self._check_preconditions(item)
        data = request.get_json(silent=True) or {}
        ctx = QueryContext(self, action=action, pk=pk, instance=item)
        try:
            data = self._run_load_hook(action, data, ctx)
            loaded = self.load_with_schema(
                data,
                instance=item,
                partial=(action == 'patch'),
                action=action,
            )
        except ValidationError as err:
            return self._validation_error(err)
        instance = self.normalize_loaded(loaded, instance=item)
        ctx.instance = instance
        ctx.item = instance
        hook_res = self.run_hook('before_update', instance, data, ctx)
        if isinstance(hook_res, dict):
            for k, v in hook_res.items():
                setattr(instance, k, v)
        elif hook_res is not None:
            instance = hook_res
            ctx.instance = instance
            ctx.item = instance
        error = self._commit('Could not update record')
        if error:
            return error
        instance = self.run_hook('after_update', instance, ctx)
        status = self.response_code.get(
            action, self.response_code.get('update', 200)
        )
        payload = self.envelope_response(
            self.dump_with_schema(instance, action=action)
        )
        etag = self._etag_for(instance)
        if etag:
            return payload, status, {'ETag': etag}
        return payload, status

    def handle_patch(self, pk):
        return self.handle_update(pk, action='patch')

    def handle_delete(self, pk):
        item = self._get_item(pk)
        if item is None:
            return self._not_found()
        self._check_preconditions(item)
        ctx = QueryContext(self, action='delete', pk=pk, instance=item)
        item = self.run_hook('before_delete', item, ctx)
        error = self._commit('Could not delete record', delete=item)
        if error:
            return error
        self.run_hook('after_delete', item, ctx)
        status = self.response_code.get('delete', 200)
        if status == 204:
            return Response(status=204)
        return {'message': 'Record deleted'}, status
