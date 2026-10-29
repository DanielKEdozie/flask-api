from flask import Response, request
from marshmallow import ValidationError
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from .hooks import ResourceHookMixin
from .query import QueryContext, ResourceQueryMixin
from .schema import ResourceSchemaMixin


class ResourceCRUDMixin(ResourceSchemaMixin, ResourceQueryMixin, ResourceHookMixin):
    """Default list/create/detail/update/delete handlers. Each handler is
    overridable per subclass; HTTP verbs dispatch here — names are
    ``handle_*`` prefixed so they never shadow the verb methods themselves.
    """

    NOT_FOUND = 'Record not found'

    def _not_found(self):
        return self.error_response(404, message=self.NOT_FOUND)

    def _validation_error(self, err):
        return self.error_response(
            ValidationError, error=err, message=err.messages
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
            return self.error_response(
                SQLAlchemyError, error=exc, message=failure_message
            )
        return None

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
        return (
            self.envelope_response(self.dump_with_schema(item, action='item')),
            self.response_code.get('item', 200),
        )

    def handle_create(self):
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
        return (
            self.envelope_response(self.dump_with_schema(instance, action='create')),
            self.response_code.get('create', 201),
        )

    def handle_update(self, pk, *, action='update'):
        item = self._get_item(pk)
        if item is None:
            return self._not_found()
        data = request.get_json(silent=True) or {}
        ctx = QueryContext(self, action=action, pk=pk, instance=item)
        try:
            data = self._run_load_hook(action, data, ctx)
            loaded = self.load_with_schema(data, instance=item, partial=True, action=action)
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
        return (
            self.envelope_response(self.dump_with_schema(instance, action=action)),
            status,
        )

    def handle_patch(self, pk):
        return self.handle_update(pk, action='patch')

    def handle_delete(self, pk):
        item = self._get_item(pk)
        if item is None:
            return self._not_found()
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
