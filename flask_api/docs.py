from __future__ import annotations

import inspect
import re
from typing import Any

from flask import Blueprint, Flask, Response, jsonify, url_for
from marshmallow import Schema, fields as ma_fields
import sqlalchemy as sa


SWAGGER_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} - Swagger UI</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
  <style>
    html {{ box-sizing: border-box; overflow-y: scroll; }}
    *, *:before, *:after {{ box-sizing: inherit; }}
    body {{ margin: 0; background: #fafafa; }}
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js" crossorigin></script>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-standalone-preset.js" crossorigin></script>
  <script>
    window.onload = () => {{
      window.ui = SwaggerUIBundle({{
        url: "{openapi_url}",
        dom_id: '#swagger-ui',
        deepLinking: true,
        presets: [
          SwaggerUIBundle.presets.apis,
          SwaggerUIStandalonePreset
        ],
        layout: "StandaloneLayout",
        persistAuthorization: true
      }});
    }};
  </script>
</body>
</html>
"""

REDOC_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title} - ReDoc</title>
  <link href="https://fonts.googleapis.com/css?family=Montserrat:300,400,700|Roboto:300,400,700" rel="stylesheet">
  <style>
    body {{ margin: 0; padding: 0; }}
  </style>
</head>
<body>
  <redoc spec-url="{openapi_url}"></redoc>
  <script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"></script>
</body>
</html>
"""


def normalize_doc_targets(doc_value: Any) -> set[str]:
    """Normalize ``doc`` / ``doc_ui`` into a set containing ``'swagger'`` and/or ``'redoc'``."""
    if doc_value is None or doc_value is True:
        return {'swagger', 'redoc'}
    if doc_value is False:
        return set()
    if isinstance(doc_value, str):
        items = [doc_value]
    elif isinstance(doc_value, (list, tuple, set)):
        items = list(doc_value)
    else:
        return {'swagger', 'redoc'}

    targets: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().lower()
        if cleaned in ('all', 'both', '*'):
            targets.update({'swagger', 'redoc'})
        elif cleaned in ('swagger', 'swagger-ui', 'swagger_ui'):
            targets.add('swagger')
        elif cleaned in ('redoc', 'redocs', 're-doc'):
            targets.add('redoc')
    return targets


def _humanize_name(name: str) -> str:
    return name.replace('_', ' ').replace('-', ' ').strip().title()


def _werkzeug_rule_to_openapi_path(rule: str) -> tuple[str, list[tuple[str, str]]]:
    """Convert ``/api/items/<int:pk>`` into ``('/api/items/{pk}', [('pk', 'int')])``."""
    params: list[tuple[str, str]] = []

    def _repl(match: re.Match) -> str:
        converter = match.group(1) or 'string'
        param_name = match.group(2)
        params.append((param_name, converter))
        return f'{{{param_name}}}'

    openapi_path = re.sub(r'<(?:([a-zA-Z_][a-zA-Z0-9_]*):)?([a-zA-Z_][a-zA-Z0-9_]*)>', _repl, rule)
    return openapi_path, params


def _converter_to_openapi_schema(converter: str | type | None) -> dict[str, Any]:
    if converter in ('int', int):
        return {'type': 'integer'}
    if converter in ('float', float):
        return {'type': 'number'}
    if converter == 'uuid':
        return {'type': 'string', 'format': 'uuid'}
    if converter is bool or converter == 'bool':
        return {'type': 'boolean'}
    return {'type': 'string'}


def _sqlalchemy_col_to_openapi_schema(model: type | None, field_name: str) -> dict[str, Any]:
    if model is None:
        return {'type': 'string'}
    col = getattr(model, field_name, None)
    col_type = None
    try:
        col_type = getattr(col.property.columns[0], 'type', None)
    except Exception:
        table = getattr(model, '__table__', None)
        if table is not None and field_name in table.columns:
            col_type = table.columns[field_name].type

    if isinstance(col_type, sa.Boolean):
        return {'type': 'boolean'}
    if isinstance(col_type, sa.Integer):
        return {'type': 'integer'}
    if isinstance(col_type, (sa.Float, sa.Numeric)):
        return {'type': 'number'}
    if isinstance(col_type, sa.DateTime):
        return {'type': 'string', 'format': 'date-time'}
    if isinstance(col_type, sa.Date):
        return {'type': 'string', 'format': 'date'}
    return {'type': 'string'}


def marshmallow_field_to_openapi(field_obj: ma_fields.Field, mode: str = 'response') -> dict[str, Any]:
    """Convert a Marshmallow ``Field`` instance into an OpenAPI 3.0 schema property dict."""
    if isinstance(field_obj, ma_fields.Nested):
        nested_schema = field_obj.schema
        inner = marshmallow_schema_to_openapi(nested_schema, mode=mode)
        if getattr(field_obj, 'many', False):
            prop: dict[str, Any] = {'type': 'array', 'items': inner}
        else:
            prop = inner
    elif isinstance(field_obj, ma_fields.List):
        inner_field = getattr(field_obj, 'inner', None)
        items_schema = (
            marshmallow_field_to_openapi(inner_field, mode=mode)
            if inner_field is not None
            else {'type': 'string'}
        )
        prop = {'type': 'array', 'items': items_schema}
    elif isinstance(field_obj, ma_fields.Dict):
        prop = {'type': 'object', 'additionalProperties': True}
    elif isinstance(field_obj, ma_fields.Boolean):
        prop = {'type': 'boolean'}
    elif isinstance(field_obj, ma_fields.Integer):
        prop = {'type': 'integer'}
    elif isinstance(field_obj, ma_fields.Decimal):
        prop = (
            {'type': 'string', 'format': 'decimal'}
            if getattr(field_obj, 'as_string', False)
            else {'type': 'number'}
        )
    elif isinstance(field_obj, (ma_fields.Float, ma_fields.Number)):
        prop = {'type': 'number'}
    elif isinstance(field_obj, ma_fields.DateTime):
        prop = {'type': 'string', 'format': 'date-time'}
    elif isinstance(field_obj, ma_fields.Date):
        prop = {'type': 'string', 'format': 'date'}
    elif isinstance(field_obj, ma_fields.Time):
        prop = {'type': 'string', 'format': 'time'}
    elif isinstance(field_obj, ma_fields.UUID):
        prop = {'type': 'string', 'format': 'uuid'}
    elif isinstance(field_obj, ma_fields.Email):
        prop = {'type': 'string', 'format': 'email'}
    elif isinstance(field_obj, ma_fields.Url):
        prop = {'type': 'string', 'format': 'uri'}
    else:
        prop = {'type': 'string'}

    if getattr(field_obj, 'allow_none', False):
        prop = {**prop, 'nullable': True}

    metadata = getattr(field_obj, 'metadata', {}) or {}
    if 'description' in metadata:
        prop['description'] = str(metadata['description'])
    if 'example' in metadata:
        prop['example'] = metadata['example']

    load_default = getattr(field_obj, 'load_default', ma_fields.missing_)
    if load_default is not ma_fields.missing_ and not callable(load_default) and load_default is not None:
        prop.setdefault('default', load_default)

    return prop


def marshmallow_schema_to_openapi(
    schema_or_cls: Schema | type | None,
    *,
    mode: str = 'response',
    partial: bool = False,
) -> dict[str, Any]:
    """Convert a Marshmallow ``Schema`` into an OpenAPI 3.0 ``object`` schema.

    ``mode``:
        - ``'response'``: excludes ``load_only=True`` fields
        - ``'request'``: excludes ``dump_only=True`` fields; populates ``required`` unless ``partial=True``
    """
    if schema_or_cls is None:
        return {'type': 'object'}
    schema = schema_or_cls() if isinstance(schema_or_cls, type) else schema_or_cls
    declared_fields = getattr(schema, 'fields', None) or getattr(schema, '_declared_fields', {})

    properties: dict[str, Any] = {}
    required_fields: list[str] = []

    for field_name, field_obj in declared_fields.items():
        if mode == 'request' and getattr(field_obj, 'dump_only', False):
            continue
        if mode == 'response' and getattr(field_obj, 'load_only', False):
            continue

        out_name = getattr(field_obj, 'data_key', None) or field_name
        properties[out_name] = marshmallow_field_to_openapi(field_obj, mode=mode)

        if mode == 'request' and not partial and getattr(field_obj, 'required', False):
            required_fields.append(out_name)

    result: dict[str, Any] = {
        'type': 'object',
        'properties': properties,
    }
    if required_fields:
        result['required'] = required_fields
    return result


def _wrap_envelope_schema(
    item_schema: dict[str, Any],
    envelope_key: str | None,
) -> dict[str, Any]:
    if not envelope_key:
        return item_schema
    return {
        'type': 'object',
        'properties': {
            envelope_key: item_schema,
        },
    }


def _wrap_list_schema(
    item_schema: dict[str, Any],
    items_key: str,
    paginate: bool,
) -> dict[str, Any]:
    props: dict[str, Any] = {
        items_key: {
            'type': 'array',
            'items': item_schema,
        },
    }
    if paginate:
        props['meta'] = {
            'type': 'object',
            'properties': {
                'page': {'type': 'integer', 'example': 1},
                'per_page': {'type': 'integer', 'example': 10},
                'total': {'type': 'integer', 'example': 1},
                'pages': {'type': 'integer', 'example': 1},
            },
        }
    return {
        'type': 'object',
        'properties': props,
    }


def _has_auth_decorators(decorators: list[Any] | tuple[Any, ...]) -> bool:
    return bool(decorators)


def _normalize_action_doc(
    raw_action_doc: str | dict[str, Any] | None,
) -> dict[str, Any]:
    if raw_action_doc is None:
        return {}
    if isinstance(raw_action_doc, str):
        return {'summary': raw_action_doc, 'description': raw_action_doc}
    if isinstance(raw_action_doc, dict):
        return dict(raw_action_doc)
    return {}


def build_openapi_spec(api) -> dict[str, Any]:
    """Generate a complete OpenAPI 3.0.3 specification dictionary for ``api``."""
    docs_cfg = dict(getattr(api, 'docs_config', None) or {})
    title = docs_cfg.get('title') or 'API Documentation'
    version = str(docs_cfg.get('version') or '1.0.0')
    description = docs_cfg.get('description') or ''

    info: dict[str, Any] = {
        'title': title,
        'version': version,
    }
    if description:
        info['description'] = description
    if docs_cfg.get('terms_of_service'):
        info['termsOfService'] = docs_cfg['terms_of_service']
    if docs_cfg.get('contact'):
        info['contact'] = docs_cfg['contact']
    if docs_cfg.get('license'):
        info['license'] = docs_cfg['license']

    bp_prefix = ''
    app_or_bp = getattr(api, 'app_or_bp', None)
    if isinstance(app_or_bp, Blueprint):
        if getattr(app_or_bp, 'url_prefix', None):
            bp_prefix = '/' + app_or_bp.url_prefix.strip('/')
        else:
            try:
                from flask import current_app
                if current_app:
                    spec_ep = f"{app_or_bp.name}.openapi_spec"
                    rules = current_app.url_map._rules_by_endpoint.get(spec_ep)
                    if rules:
                        rule_str = rules[0].rule
                        suffix = '/' + (docs_cfg.get('openapi_url') or '/openapi.json').strip('/')
                        if rule_str.endswith(suffix):
                            bp_prefix = rule_str[:-len(suffix)]
            except Exception:
                pass

    paths: dict[str, dict[str, Any]] = {}
    tags_by_name: dict[str, dict[str, Any]] = {}
    for explicit_tag in docs_cfg.get('tags') or ():
        if isinstance(explicit_tag, dict) and explicit_tag.get('name'):
            tags_by_name[explicit_tag['name']] = dict(explicit_tag)

    schemas_components: dict[str, Any] = {}

    for resource in getattr(api, 'resources', ()):
        res_docs = dict(getattr(resource, 'docs', None) or {})
        if res_docs.get('exclude') or res_docs.get('hidden'):
            continue

        res_name = (
            getattr(resource, 'resource_name', None)
            or resource._resolve_resource_name(None)
        )
        model = getattr(resource, 'model', None)
        model_label = getattr(model, '__name__', None) or _humanize_name(res_name)

        default_tag = (
            res_docs.get('tag')
            or (res_docs.get('tags')[0] if isinstance(res_docs.get('tags'), (list, tuple)) and res_docs.get('tags') else None)
            or _humanize_name(res_name)
        )
        default_tags = (
            list(res_docs['tags'])
            if isinstance(res_docs.get('tags'), (list, tuple)) and res_docs.get('tags')
            else [default_tag]
        )

        tag_desc = res_docs.get('description') or res_docs.get('summary')
        for t_name in default_tags:
            if t_name not in tags_by_name:
                t_entry: dict[str, Any] = {'name': t_name}
                if tag_desc:
                    t_entry['description'] = tag_desc
                tags_by_name[t_name] = t_entry
            elif tag_desc and not tags_by_name[t_name].get('description'):
                tags_by_name[t_name]['description'] = tag_desc

        # Register component schemas for this resource
        schema_base_name = getattr(getattr(resource, 'schema', None).__class__, '__name__', f'{model_label}Schema')
        if schema_base_name.endswith('Schema') and len(schema_base_name) > 6:
            schema_base_name = schema_base_name[:-6]

        resp_schema_name = f'{schema_base_name}Response'
        req_schema_name = f'{schema_base_name}Request'
        patch_schema_name = f'{schema_base_name}PatchRequest'

        schemas_components[resp_schema_name] = marshmallow_schema_to_openapi(
            resource.schema, mode='response'
        )
        schemas_components[req_schema_name] = marshmallow_schema_to_openapi(
            resource.schema, mode='request', partial=False
        )
        schemas_components[patch_schema_name] = marshmallow_schema_to_openapi(
            resource.schema, mode='request', partial=True
        )

        item_ref = {'$ref': f'#/components/schemas/{resp_schema_name}'}
        req_ref = {'$ref': f'#/components/schemas/{req_schema_name}'}
        patch_ref = {'$ref': f'#/components/schemas/{patch_schema_name}'}

        # Normalize per-action docs inside resource `docs={'resources': {...}}` or `docs={'actions': {...}}`
        raw_per_action = (
            res_docs.get('resources')
            or res_docs.get('actions')
            or res_docs.get('operations')
            or {}
        )
        per_action_docs: dict[str, dict[str, Any]] = {}
        if isinstance(raw_per_action, dict):
            for act_key, act_val in raw_per_action.items():
                try:
                    canonical = resource.resolve_action_name(act_key)
                except ValueError:
                    canonical = str(act_key).strip().lower()
                per_action_docs[canonical] = _normalize_action_doc(act_val)

        # Also allow top-level action keys inside `docs={...}` directly if someone wrote `docs={'tag': '...', 'list': {...}}`
        for act_candidate in ('list', 'item', 'retrieve', 'detail', 'create', 'update', 'patch', 'patch_update', 'delete'):
            if act_candidate in res_docs and act_candidate not in ('tag', 'tags', 'description', 'summary', 'resources', 'actions', 'operations', 'exclude', 'hidden'):
                canonical = resource.resolve_action_name(act_candidate)
                per_action_docs.setdefault(canonical, _normalize_action_doc(res_docs[act_candidate]))

        prefix = resource._resolve_prefix(app_or_bp, resource.route_prefix)
        base = (resource.path or res_name).strip('/')
        collection_rule = f'{bp_prefix}{prefix}/{base}'
        converter = resource.pk_type
        pk_part = f'<{converter}:pk>' if converter else '<pk>'
        detail_rule = (
            collection_rule
            if resource.singleton
            else f'{collection_rule}/{pk_part}'
        )

        exposed_actions = resource.resolve_exposed_actions()
        action_Method_and_rule: list[tuple[str, str, str]] = []
        if resource.singleton:
            action_Method_and_rule.extend([
                ('item', 'get', collection_rule),
                ('create', 'post', collection_rule),
                ('update', 'put', collection_rule),
                ('patch', 'patch', collection_rule),
                ('delete', 'delete', collection_rule),
            ])
        else:
            action_Method_and_rule.extend([
                ('list', 'get', collection_rule),
                ('create', 'post', collection_rule),
            ])
            if resource.detail:
                action_Method_and_rule.extend([
                    ('item', 'get', detail_rule),
                    ('update', 'put', detail_rule),
                    ('patch', 'patch', detail_rule),
                    ('delete', 'delete', detail_rule),
                ])
        action_Method_and_rule = [
            (act, method, rule)
            for act, method, rule in action_Method_and_rule
            if act in exposed_actions
        ]

        default_summaries = {
            'list': f'List {_humanize_name(res_name).lower()}',
            'item': f'Retrieve {model_label.lower()}',
            'create': f'Create {model_label.lower()}',
            'update': f'Update {model_label.lower()}',
            'patch': f'Partially update {model_label.lower()}',
            'delete': f'Delete {model_label.lower()}',
        }

        norm_decorators = resource.normalize_method_decorators(
            getattr(resource, 'method_decorators', ())
        )
        ep_names = resource.resolve_endpoint_names()
        base_ep = re.sub(r'\W+', '_', base) if resource.path else res_name

        for action, http_method, raw_rule in action_Method_and_rule:
            act_doc = per_action_docs.get(action, {})
            if act_doc.get('exclude') or act_doc.get('hidden'):
                continue

            openapi_path, path_params = _werkzeug_rule_to_openapi_path(raw_rule)
            parameters: list[dict[str, Any]] = []

            for param_name, param_conv in path_params:
                param_spec = (resource.route_params or {}).get(param_name) or {}
                schema_type = _converter_to_openapi_schema(
                    param_spec.get('type') or param_conv
                )
                parameters.append({
                    'name': param_name,
                    'in': 'path',
                    'required': True,
                    'schema': schema_type,
                    'description': param_spec.get('description') or f'{_humanize_name(param_name)} identifier',
                })

            if action == 'list':
                if getattr(resource, 'paginate', True):
                    parameters.extend([
                        {
                            'name': 'page',
                            'in': 'query',
                            'required': False,
                            'schema': {'type': 'integer', 'default': 1, 'minimum': 1},
                            'description': 'Page number.',
                        },
                        {
                            'name': 'per_page',
                            'in': 'query',
                            'required': False,
                            'schema': {
                                'type': 'integer',
                                'default': getattr(resource, 'page_size', 10),
                                'maximum': getattr(resource, 'max_page_size', 50),
                            },
                            'description': 'Items per page.',
                        },
                        {
                            'name': 'paginate',
                            'in': 'query',
                            'required': False,
                            'schema': {'type': 'boolean', 'default': True},
                            'description': 'Set false to return all matching records without pagination.',
                        },
                    ])
                if getattr(resource, 'search_fields', ()):
                    fields_str = ', '.join(resource.search_fields)
                    parameters.append({
                        'name': 'search',
                        'in': 'query',
                        'required': False,
                        'schema': {'type': 'string'},
                        'description': f'Case-insensitive search across: {fields_str}.',
                    })
                for f_name in getattr(resource, 'filter_fields', ()):
                    caster = (getattr(resource, 'filter_types', None) or {}).get(f_name)
                    f_schema = (
                        _converter_to_openapi_schema(caster)
                        if caster is not None
                        else _sqlalchemy_col_to_openapi_schema(model, f_name)
                    )
                    parameters.append({
                        'name': f_name,
                        'in': 'query',
                        'required': False,
                        'schema': f_schema,
                        'description': f'Filter by {f_name}.',
                    })
                sort_fields = list(getattr(resource, 'sort_fields', ()) or ())
                if sort_fields or getattr(resource, 'default_sort_field', ''):
                    sort_schema: dict[str, Any] = {'type': 'string'}
                    if sort_fields:
                        sort_schema['enum'] = sort_fields
                    if getattr(resource, 'default_sort_field', ''):
                        sort_schema['default'] = resource.default_sort_field
                    parameters.extend([
                        {
                            'name': 'sort',
                            'in': 'query',
                            'required': False,
                            'schema': sort_schema,
                            'description': 'Column to sort by.',
                        },
                        {
                            'name': 'sort_order',
                            'in': 'query',
                            'required': False,
                            'schema': {
                                'type': 'string',
                                'enum': ['asc', 'desc'],
                                'default': getattr(resource, 'sort_order', 'asc'),
                            },
                            'description': 'Sort direction.',
                        },
                    ])

            if act_doc.get('parameters'):
                parameters.extend(act_doc['parameters'])

            op_tags = (
                [act_doc['tag']]
                if act_doc.get('tag')
                else (list(act_doc['tags']) if act_doc.get('tags') else default_tags)
            )
            for t_name in op_tags:
                tags_by_name.setdefault(t_name, {'name': t_name})

            summary = act_doc.get('summary') or default_summaries.get(action, action.title())
            op_description = act_doc.get('description') or summary
            op_id = act_doc.get('operation_id') or f"{base_ep}_{ep_names.get(action, action)}"

            status_code = str(
                resource.response_code.get(
                    action,
                    resource.response_code.get('update', 200) if action == 'patch' else (201 if action == 'create' else 200),
                )
            )

            if action == 'list':
                resp_body_schema = _wrap_list_schema(
                    item_ref,
                    getattr(resource, 'items_key', 'items') or 'items',
                    bool(getattr(resource, 'paginate', True)),
                )
            elif action == 'delete':
                resp_body_schema = {
                    'type': 'object',
                    'properties': {'message': {'type': 'string', 'example': 'Record deleted'}},
                }
            else:
                resp_body_schema = _wrap_envelope_schema(
                    item_ref, getattr(resource, 'envelope_key', None)
                )

            responses: dict[str, Any] = {
                status_code: {
                    'description': 'No Content' if status_code == '204' else 'Successful response',
                    **(
                        {}
                        if status_code == '204'
                        else {
                            'content': {
                                'application/json': {'schema': resp_body_schema}
                            }
                        }
                    ),
                }
            }
            if action in ('item', 'update', 'patch', 'delete'):
                responses['404'] = {'description': 'Record not found'}
            if action in ('create', 'update', 'patch'):
                responses['400'] = {'description': 'Validation error'}
            if act_doc.get('responses') and isinstance(act_doc['responses'], dict):
                responses.update(act_doc['responses'])

            operation: dict[str, Any] = {
                'tags': op_tags,
                'summary': summary,
                'description': op_description,
                'operationId': op_id,
                'responses': responses,
            }
            if parameters:
                operation['parameters'] = parameters
            if act_doc.get('deprecated'):
                operation['deprecated'] = True

            if action in ('create', 'update', 'patch'):
                body_ref = patch_ref if action == 'patch' else req_ref
                operation['requestBody'] = {
                    'required': True,
                    'content': {
                        'application/json': {'schema': body_ref},
                    },
                }

            effective_decs = (
                list(norm_decorators.get('all', ()))
                + list(norm_decorators.get(action, ()))
            )
            if _has_auth_decorators(effective_decs) or act_doc.get('security'):
                operation['security'] = act_doc.get('security') or [
                    {'BearerAuth': []},
                    {'SessionCookie': []},
                ]
                responses.setdefault('401', {'description': 'Authentication required'})

            paths.setdefault(openapi_path, {})[http_method] = operation

        # Include custom @resource.route endpoints registered on this resource
        for custom_rt in getattr(resource, '_custom_routes', ()):
            rt_docs = dict(custom_rt.get('docs') or {})
            if rt_docs.get('exclude') or rt_docs.get('hidden'):
                continue

            raw_url = f"{bp_prefix}{custom_rt['url']}"
            openapi_path, path_params = _werkzeug_rule_to_openapi_path(raw_url)
            fn = custom_rt['fn']
            fn_doc = (inspect.getdoc(fn) or '').strip()
            first_line = fn_doc.splitlines()[0] if fn_doc else _humanize_name(fn.__name__)

            parameters = []
            for param_name, param_conv in path_params:
                param_spec = (custom_rt.get('route_params') or {}).get(param_name) or {}
                parameters.append({
                    'name': param_name,
                    'in': 'path',
                    'required': True,
                    'schema': _converter_to_openapi_schema(param_spec.get('type') or param_conv),
                })
            if rt_docs.get('parameters'):
                parameters.extend(rt_docs['parameters'])

            op_tags = (
                [rt_docs['tag']]
                if rt_docs.get('tag')
                else (list(rt_docs['tags']) if rt_docs.get('tags') else default_tags)
            )
            for t_name in op_tags:
                tags_by_name.setdefault(t_name, {'name': t_name})

            for m in custom_rt['methods']:
                http_method = m.lower()
                op_id = rt_docs.get('operation_id') or f"{custom_rt['endpoint']}_{http_method}"
                summary = rt_docs.get('summary') or first_line
                op_desc = rt_docs.get('description') or fn_doc or summary

                resp_schema_obj = rt_docs.get('response_schema') or custom_rt.get('schemas') or resource.schema
                if isinstance(resp_schema_obj, dict):
                    resp_schema_obj = resp_schema_obj.get(m.upper()) or resp_schema_obj.get('*') or resource.schema
                resp_openapi = marshmallow_schema_to_openapi(resp_schema_obj, mode='response')

                operation = {
                    'tags': op_tags,
                    'summary': summary,
                    'description': op_desc,
                    'operationId': op_id,
                    'responses': rt_docs.get('responses') or {
                        '200': {
                            'description': 'Successful response',
                            'content': {
                                'application/json': {'schema': resp_openapi}
                            },
                        }
                    },
                }
                if parameters:
                    operation['parameters'] = parameters

                if m.upper() in ('POST', 'PUT', 'PATCH') and custom_rt.get('validate', True):
                    req_schema_obj = custom_rt.get('schemas') or resource.schema
                    if isinstance(req_schema_obj, dict):
                        req_schema_obj = req_schema_obj.get(m.upper()) or req_schema_obj.get('*') or resource.schema
                    req_openapi = marshmallow_schema_to_openapi(
                        req_schema_obj,
                        mode='request',
                        partial=(m.upper() == 'PATCH'),
                    )
                    operation['requestBody'] = {
                        'required': True,
                        'content': {
                            'application/json': {'schema': req_openapi}
                        },
                    }

                custom_decs = list(norm_decorators.get('all', ())) + list(custom_rt.get('decorators') or ())
                if _has_auth_decorators(custom_decs) or rt_docs.get('security'):
                    operation['security'] = rt_docs.get('security') or [
                        {'BearerAuth': []},
                        {'SessionCookie': []},
                    ]

                paths.setdefault(openapi_path, {})[http_method] = operation

    security_schemes = docs_cfg.get('security_schemes') or {
        'BearerAuth': {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'JWT',
            'description': 'JWT Bearer access token',
        },
        'SessionCookie': {
            'type': 'apiKey',
            'in': 'cookie',
            'name': 'session',
            'description': 'Flask-Login session cookie',
        },
    }

    spec: dict[str, Any] = {
        'openapi': '3.0.3',
        'info': info,
        'tags': list(tags_by_name.values()),
        'paths': paths,
        'components': {
            'schemas': schemas_components,
            'securitySchemes': security_schemes,
        },
    }
    if docs_cfg.get('servers'):
        spec['servers'] = docs_cfg['servers']
    if docs_cfg.get('external_docs'):
        spec['externalDocs'] = docs_cfg['external_docs']
    return spec


def mount_api_docs(api, app_or_bp: Flask | Blueprint) -> None:
    """Register OpenAPI JSON spec, Swagger UI, and/or ReDoc routes on ``app_or_bp``."""
    if getattr(api, '_docs_mounted', False):
        return

    docs_cfg = dict(getattr(api, 'docs_config', None) or {})
    targets = normalize_doc_targets(getattr(api, 'doc_targets', None))
    title = docs_cfg.get('title') or 'API Documentation'

    openapi_path = '/' + (docs_cfg.get('openapi_url') or '/openapi.json').strip('/')
    swagger_path = '/' + (docs_cfg.get('swagger_url') or '/docs').strip('/')
    redoc_path = '/' + (docs_cfg.get('redoc_url') or '/redoc').strip('/')

    spec_endpoint_name = 'openapi_spec'
    full_spec_endpoint = (
        f'{app_or_bp.name}.{spec_endpoint_name}'
        if isinstance(app_or_bp, Blueprint)
        else spec_endpoint_name
    )

    def openapi_json_view():
        return jsonify(api.get_openapi_spec())

    app_or_bp.add_url_rule(
        openapi_path,
        endpoint=spec_endpoint_name,
        view_func=openapi_json_view,
        methods=['GET'],
    )

    if 'swagger' in targets:
        def swagger_ui_view():
            spec_url = url_for(full_spec_endpoint)
            html = SWAGGER_UI_HTML.format(title=title, openapi_url=spec_url)
            return Response(html, mimetype='text/html')

        app_or_bp.add_url_rule(
            swagger_path,
            endpoint='swagger_ui',
            view_func=swagger_ui_view,
            methods=['GET'],
        )

    if 'redoc' in targets:
        def redoc_ui_view():
            spec_url = url_for(full_spec_endpoint)
            html = REDOC_HTML.format(title=title, openapi_url=spec_url)
            return Response(html, mimetype='text/html')

        app_or_bp.add_url_rule(
            redoc_path,
            endpoint='redoc_ui',
            view_func=redoc_ui_view,
            methods=['GET'],
        )

    api._docs_mounted = True
