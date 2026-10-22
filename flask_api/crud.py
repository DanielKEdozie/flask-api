from flask import Response, request
from marshmallow import ValidationError
import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from .hooks import ResourceHookMixin
from .query import ResourceQueryMixin
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
            self.envelope_response(self.dump_with_schema(item)),
            self.response_code.get('item', 200),
        )

    def handle_create(self):
        if self.singleton and self._get_item(None) is not None:
            return self.error_response(409, message='Record already exists')
        raw_data = request.get_json(silent=True) or {}
        pre_ctx = self.build_hook_context('create', data=raw_data)
        data = self.run_hook('before_load', raw_data, ctx=pre_ctx) or {}
        try:
            loaded = self.load_with_schema(data)
        except ValidationError as err:
            return self._validation_error(err)
        instance = self._bind_scope(self.normalize_loaded(loaded))
        ctx = self.build_hook_context(
            'create', item=instance, data=data, loaded=loaded
        )
        instance = self.run_hook('before_save', instance, data, ctx=ctx)
        ctx.item = ctx.instance = instance
        error = self._commit('Could not create record', add=instance)
        if error:
            return error
        instance = self.run_hook('after_save', instance, ctx=ctx)
        return (
            self.envelope_response(self.dump_with_schema(instance)),
            self.response_code.get('create', 201),
        )

    def handle_update(self, pk):
        item = self._get_item(pk)
        if item is None:
            return self._not_found()
        raw_data = request.get_json(silent=True) or {}
        pre_ctx = self.build_hook_context('update', pk=pk, item=item, data=raw_data)
        data = self.run_hook('before_load', raw_data, item, ctx=pre_ctx) or {}
        try:
            loaded = self.load_with_schema(data, instance=item, partial=True)
        except ValidationError as err:
            return self._validation_error(err)
        instance = self.normalize_loaded(loaded, instance=item)
        ctx = self.build_hook_context(
            'update', pk=pk, item=instance, data=data, loaded=loaded
        )
        instance = self.run_hook('before_update', instance, data, ctx=ctx)
        ctx.item = ctx.instance = instance
        error = self._commit('Could not update record')
        if error:
            return error
        instance = self.run_hook('after_update', instance, ctx=ctx)
        return (
            self.envelope_response(self.dump_with_schema(instance)),
            self.response_code.get('update', 200),
        )

    def handle_delete(self, pk):
        item = self._get_item(pk)
        if item is None:
            return self._not_found()
        ctx = self.build_hook_context('delete', pk=pk, item=item)
        item = self.run_hook('before_delete', item, ctx=ctx)
        error = self._commit('Could not delete record', delete=item)
        if error:
            return error
        self.run_hook('after_delete', item, ctx=ctx)
        status = self.response_code.get('delete', 200)
        if status == 204:
            return Response(status=204)
        return {'message': 'Record deleted'}, status
