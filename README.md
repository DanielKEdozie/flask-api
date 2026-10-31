# `flask_api` (`flask-api-resource`)

Declarative **SQLAlchemy 2.0+** and **Marshmallow** REST CRUD resources for Flask, built on `MethodView`.

---

## Table of Contents

1. [Installation](#installation)
2. [Quick Start](#quick-start)
3. [Initializing `Api` & Creating Resources](#initializing-api--creating-resources)
4. [Configuration Options Reference](#configuration-options-reference)
5. [Querying: Filtering, Searching, Sorting & Pagination](#querying-filtering-searching-sorting--pagination)
6. [Eager Loading (`eager_loads`)](#eager-loading-eager_loads)
7. [Lifecycle Hooks (`hooks`)](#lifecycle-hooks-hooks)
8. [Custom Query Hooks (`_query`)](#custom-query-hooks-_query)
9. [Authentication & Method Decorators (`method_decorators`)](#authentication--method-decorators-method_decorators)
10. [Nested Subresources & Singletons (`create_subresource`)](#nested-subresources--singletons-create_subresource)
11. [Custom Endpoints (`@resource.route`)](#custom-endpoints-resourceroute)
12. [Error Formatting & View Overrides](#error-formatting--view-overrides)
13. [Stable Error Contract & Request IDs](#stable-error-contract--request-ids)
14. [Conditional Requests (ETag / If-Match)](#conditional-requests-etag--if-match)
15. [Idempotent Creates](#idempotent-creates)

---

## Installation

Install in editable or standard mode with `pip`:

```bash
pip install -e ./packages/flask-api
```

---

## Quick Start

```python
from flask import Blueprint, Flask
from flask_sqlalchemy import SQLAlchemy
from marshmallow import Schema, fields
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload

from flask_api import Api


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


class Category(db.Model):
    __tablename__ = 'categories'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    products: Mapped[list['Product']] = relationship('Product', back_populates='category')


class Product(db.Model):
    __tablename__ = 'products'

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    price: Mapped[float] = mapped_column(sa.Float, nullable=False)
    in_stock: Mapped[bool] = mapped_column(sa.Boolean, default=True)
    category_id: Mapped[int] = mapped_column(sa.ForeignKey('categories.id'), nullable=False)
    category: Mapped['Category'] = relationship('Category', back_populates='products')


class ProductSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str(required=True)
    price = fields.Float(required=True)
    in_stock = fields.Bool(load_default=True)
    category_id = fields.Int(required=True)


def create_app():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///store.db'
    db.init_app(app)

    api_bp = Blueprint('api', __name__, url_prefix='/api')
    api = Api(
        api_bp,
        db=db,
        envelope_key='data',
        items_key='records',
    )

    product_res = api.create_resources(
        model=Product,
        schema=ProductSchema,
        eager_loads=[(selectinload, 'category')],
        filter_fields=['in_stock', 'category_id'],
        filter_types={'in_stock': bool, 'category_id': int},
        search_fields=['name'],
        sort_fields=['id', 'name', 'price'],
        default_sort_field='id',
    )

    app.register_blueprint(api_bp)
    return app
```

This automatically generates and mounts:
- `GET /api/products` — Paginated list with filtering, search, and sorting
- `POST /api/products` — Validate JSON via `ProductSchema` and create a record (`201 Created`)
- `GET /api/products/<int:pk>` — Retrieve a single record by primary key (`200 OK` or `404 Not Found`)
- `PUT /api/products/<int:pk>` & `PATCH /api/products/<int:pk>` — Partial update of a record (`200 OK`)
- `DELETE /api/products/<int:pk>` — Delete a record (`200 OK` or `204 No Content`)

---

## Initializing `Api` & Creating Resources

### Direct or Deferred Initialization (`init_app`)

```python
from flask_api import Api

# 1. Bound directly to a Blueprint or Flask app
api = Api(api_bp, db=db, envelope_key='data', items_key='items', page_size=20)

# 2. Or using the factory pattern
api = Api()
api.init_app(api_bp, db=db, envelope_key='data', items_key='items')
```

Any default option passed to `Api(...)` (such as `db`, `envelope_key`, `items_key`, `page_size`, `max_page_size`, `method_decorators`, `errors`) is automatically inherited by every resource created through that `api` instance.

### Creating Single or Multiple Resources (`create_resources` / `create_resource`)

```python
# 1. Single resource via keyword arguments
product_api = api.create_resources(
    model=Product,
    schema=ProductSchema,
)
# (Also available as api.create_resource(model=Product, schema=ProductSchema))

# 2. Multiple resources via positional dicts (or a list of dicts)
category_api, product_api = api.create_resources(
    {'model': Category, 'schema': CategorySchema},
    {'model': Product, 'schema': ProductSchema, 'page_size': 25},
)

# 3. Multiple resources via named keyword dicts (sets resource_name to the key)
resources = api.create_resources(
    categories={'model': Category, 'schema': CategorySchema},
    products={'model': Product, 'schema': ProductSchema},
)
# Access via resources['categories'], resources['products']
```

---

## Configuration Options Reference

| Option | Default | Description |
| :--- | :--- | :--- |
| `model` | *Required* | SQLAlchemy 2.0+ model class. |
| `schema` | `None` | Default Marshmallow `Schema` class or instance (required unless `schemas` is provided). |
| `schemas` | `{}` | Per-action custom schema mapping (e.g. `{'list': ListSchema, 'retrieve': DetailSchema}`). |
| `db` | Auto-detected | Optional Flask-SQLAlchemy `db` instance (auto-discovered from `current_app` if omitted). |
| `resource_name` | `model.__tablename__` | URL segment and endpoint base name (e.g., `'products'`). |
| `route_prefix` | `'/api'` (App) or `''` (Blueprint) | URL prefix prepended to the resource routes. |
| `path` | `resource_name` | Custom URL template relative to `route_prefix`. |
| `pk_field` | `'id'` | Model attribute used for detail lookup. |
| `pk_type` | `'int'` | Werkzeug URL converter for `<pk>` (`'int'`, `'string'`, `'uuid'`, or `None`). |
| `detail` | `True` | Whether to mount the `/<pk>` detail routes (`GET`, `PUT`, `PATCH`, `DELETE`). |
| `singleton` | `False` | Serve a single record at `path` without `<pk>` (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`). |
| `envelope_key` | `None` | Wrap single-item responses (`detail`, `create`, `update`) in `{envelope_key: data}`. |
| `items_key` | `'items'` | Key used for the list array in `GET` list responses (`{items_key: [...], 'meta': {...}}`). |
| `paginate` | `True` | Paginate list responses by default (clients can toggle via `?paginate=false`). |
| `page_size` | `10` | Default items per page (`?per_page=`). |
| `max_page_size` | `50` | Maximum allowed `?per_page=` value. |
| `max_unpaginated` | `1000` | Safety limit for `?paginate=false`; larger result sets raise `400 unpaginated_response_too_large`. Set to `None` to disable. |
| `filter_fields` | `()` | Whitelist of model column names allowed for exact-match filtering (`?field=value`). |
| `filter_types` | `{}` | Mapping of `{field: callable}` type casters for filter values (e.g., `{'in_stock': bool, 'category_id': int}`). |
| `search_fields` | `()` | Model string column names searched with case-insensitive `ILIKE` via `?search=term`. |
| `sort_fields` | `()` | Whitelist of model columns allowed for `?sort=field` (allows any model column if empty). |
| `default_sort_field` | `''` | Default column used to sort list queries when `?sort=` is omitted. |
| `sort_order` | `'asc'` | Default sort direction (`'asc'` or `'desc'`), overridden via `?sort_order=desc`. |
| `eager_loads` | `()` | Eager loading specifications applied to list and detail queries. |
| `hooks` | `{}` | Lifecycle hooks (`before_save`, `after_save`, `before_update`, `after_update`, `before_delete`, `after_delete`). Factory hooks are merged with hooks defined on the resource subclass. |
| `_query` | `{}` | Per-action query modifiers (`{'list': fn, 'item': fn}`). |
| `method_decorators` | `()` | Decorators applied to all or specific CRUD actions. |
| `methods` | `None` | Whitelist of HTTP methods or action names to expose (e.g. `['GET', 'POST']` or `['list', 'retrieve', 'create']`). Excludes all others (405). |
| `endpoint_names` | `{}` | Custom per-action endpoint suffixes for `url_for` and OpenAPI `operationId` (e.g. `{'list': 'collection'}`). |
| `docs` | `{}` | OpenAPI/Swagger documentation metadata (`tag`, `description`, `resources={'list': {...}}`). |
| `view_overrides` | `{}` | Custom handler functions overriding `list`, `item`, `create`, `update`, or `delete`. |
| `response_code` | `{}` | Override default HTTP status codes per action (`{'delete': 204}` returns an empty 204 body). |
| `errors` | `{}` | Custom error formatters keyed by HTTP status code (`404`) or exception class (`ValidationError`). Handlers are matched along the exception MRO. |
| `etag_enabled` | `True` | Emit `ETag` on detail/write responses and honor `If-None-Match` (304) and `If-Match` (412). |
| `require_if_match` | `False` | Require `If-Match` on updates and deletes, returning `428` when missing. |
| `idempotency_store` | `None` | Store with `reserve`/`complete`/`release`; enables `Idempotency-Key` replay for creates. |
| `idempotency_header` | `'Idempotency-Key'` | Request header carrying the idempotency key. |
| `idempotency_ttl` | `86400` | Seconds a completed idempotent response is retained. |
| `idempotency_scope` | `None` | Optional `() -> str` identity used to scope keys (defaults to `g.current_user` / `g.user`). |
| `error_docs_url` | `None` | Base URL exposed as `error.documentation_url` in default error payloads. |

Legacy aliases are still accepted: `pagination_item_key` → `items_key`, `apply_eager` → `eager_loads`, and `default_sort_order` → `sort_order`. Unknown options now raise `ValueError` instead of being silently ignored.

---

## Custom & Per-Action Schemas (`schemas`)

By default, passing `schema=MySchema` applies that schema across all CRUD actions. You can use `schemas={...}` to provide specialized schemas for individual actions (e.g. lightweight summary schema for `list`, full schema for `retrieve`, separate request/response schemas for `create`, etc.):

```python
api.create_resources(
    model=Product,
    schemas={
        # List GET: lightweight summary schema
        'list': ProductListSchema,

        # Detail GET: full record schema
        'retrieve': ProductDetailSchema,

        # Create POST: request schema for validation, response schema for serialization
        'create': {'request': ProductCreateSchema, 'response': ProductDetailSchema},
        # Or as a concise 2-tuple: (ProductCreateSchema, ProductDetailSchema)

        # Update PUT: validates body & dumps response
        'update': ProductUpdateSchema,

        # Optional: PATCH automatically inherits 'update' if not explicitly provided
        'patch': ProductPatchSchema,
    },
)
```

### Action Aliases & Case-Insensitive Keys
Any standard action name or HTTP method verb can be used:
- **List / Collection GET**: `'list'`, `'get_list'`, `'get_collection'`, `'collection'`, `'GET_LIST'`
- **Detail / Single GET**: `'retrieve'`, `'item'`, `'detail'`, `'show'`, `'get'`, `'get_item'`, `'GET'`
- **Create POST**: `'create'`, `'post'`, `'POST'`
- **Update PUT**: `'update'`, `'put'`, `'PUT'`
- **Partial Update PATCH**: `'patch'`, `'patch_update'`, `'partial_update'`, `'PATCH'`
- **Delete**: `'delete'`, `'destroy'`, `'DELETE'`
- **Fallback / Default**: `'default'`, `'*'`, or `'all'`

### OpenAPI Docs Integration
When `enable_docs=True` is enabled, OpenAPI / Swagger UI / ReDoc automatically discovers the exact schemas resolved for each action:
- Registers corresponding components (e.g. `ProductListResponse`, `ProductDetailResponse`, `ProductCreateRequest`, `ProductUpdateRequest`, `ProductUpdatePatchRequest`).
- Accurately links `$ref` schemas in Swagger UI and ReDoc for both request bodies and response payloads.

---

## Querying: Filtering, Searching, Sorting & Pagination

Given a resource configured with:

```python
product_api = api.create_resources(
    model=Product,
    schema=ProductSchema,
    filter_fields=['in_stock', 'category_id'],
    filter_types={'in_stock': bool, 'category_id': int},
    search_fields=['name'],
    sort_fields=['id', 'name', 'price'],
    default_sort_field='id',
    sort_order='desc',
)
```

Clients can query `GET /api/products` using:
- **Filtering**: `GET /api/products?in_stock=true&category_id=3`
- **Searching**: `GET /api/products?search=organic` (runs `ILIKE '%organic%'` across `search_fields` with `OR`)
- **Sorting**: `GET /api/products?sort=price&sort_order=asc`
- **Pagination**: `GET /api/products?page=2&per_page=15` (or disable pagination with `?paginate=false`)

**Paginated Response Format:**
```json
{
  "records": [
    {"id": 1, "name": "Organic Apples", "price": 4.5, "in_stock": true, "category_id": 3}
  ],
  "meta": {
    "page": 1,
    "per_page": 15,
    "total": 1,
    "pages": 1
  }
}
```

---

## Eager Loading (`eager_loads`)

Prevent N+1 queries by passing `eager_loads` in any of three convenient formats:

```python
from sqlalchemy.orm import joinedload, selectinload

# 1. Tuple of (loader_fn, relationship_name_or_attr)
api.create_resources(
    model=Category,
    schema=CategorySchema,
    eager_loads=[
        (selectinload, 'products'),
        (joinedload, Category.parent),
    ],
)

# 2. Direct SQLAlchemy loader option objects
api.create_resources(
    model=Category,
    schema=CategorySchema,
    eager_loads=[selectinload(Category.products)],
)

# 3. Relationship attribute names (defaults to selectinload)
api.create_resources(
    model=Category,
    schema=CategorySchema,
    eager_loads=['products'],
)
```

---

## Lifecycle Hooks (`hooks`)

Run custom logic before validation, after validation, or around persistence. Hooks receive `ctx` ([`QueryContext`](file:///c:/Users/danni/OneDrive/Desktop/project-3/foodmart/packages/flask-api/flask_api/query.py#L15-L78), exposing `ctx.args`, `ctx.pk`, `ctx.view`, `ctx.model`, `ctx.schema`, and resolved parent route params):

### Pre-Validation Hooks (Before Instance Creation)
- `before_load(data: dict, ctx) -> dict | None`: Runs on the raw input payload **before** `schema.load()`. On `update`/`patch`, `ctx.instance` (or `ctx.item`) holds the existing database record being modified (or `None` on `create`). Ideal for injecting required fields (e.g. `tenant_id`, `created_by_id`, or slugifying titles) before Marshmallow validation occurs.
- `before_create_load(data: dict, ctx) -> dict | None`: Action-specific pre-load hook for `create` (POST). Overrides `before_load` if both are defined.
- `before_update_load(data: dict, ctx) -> dict | None`: Action-specific pre-load hook for `update`/`patch` (PUT/PATCH), with the existing model instance accessible via `ctx.instance` (or `ctx.item`). Overrides `before_load` if both are defined.
- `before_validate(data: dict, ctx)`: Alias for `before_load`.

### Post-Validation & Persistence Hooks (On Model Instance)
- `before_save(item, data: dict, ctx) -> item | dict | None`: Receives the instantiated (uncommitted) `item` populated with `schema.load()` values, the validated `data` dict, and `ctx`. Mutate `item` in-place, return a dict of extra attributes to set, or return a replacement instance.
- `after_save(item, ctx) -> item | None`: Receives the committed model instance `item` and `ctx` after creation.
- `before_update(item, data: dict, ctx) -> item | dict | None`: Receives the existing `item` with `schema.load(partial=True)` values already applied, the validated `data` dict, and `ctx`.
- `after_update(item, ctx) -> item | None`: Receives the committed model instance `item` and `ctx` after update.
- `before_delete(item, ctx) -> item | None`: Receives the model instance `item` and `ctx` before deletion.
- `after_delete(item, ctx) -> None`: Receives the deleted model instance `item` and `ctx` after commit.

*(Hooks with narrower legacy arities are also supported automatically via signature inspection.)*

```python
from slugify import slugify

def set_product_slug(item, data, ctx):
    if 'name' in data:
        item.slug = slugify(data['name'])

product_api = api.create_resources(
    model=Product,
    schema=ProductSchema,
    hooks={
        'before_save': set_product_slug,
        'after_save': lambda item, ctx: print(f'Created product #{item.id}'),
        'before_update': set_product_slug,
        'after_update': lambda item, ctx: print(f'Updated product #{item.id}'),
    },
)
```

---

## Custom Query Hooks (`_query`)

Customize the SQLAlchemy 2.0 `Select` statement for `list` or `item` (`show`/`detail`) actions. The hook receives `(query, ctx)` where `ctx` is a [`QueryContext`](file:///c:/Users/danni/OneDrive/Desktop/project-3/foodmart/packages/flask-api/flask_api/query.py#L15-L78) exposing `.model`, `.schema`, `.args` (`request.args`), `.pk`, `.view`, and any resolved parent bindings:

```python
product_api = api.create_resources(
    model=Product,
    schema=ProductSchema,
    _query={
        'list': lambda query, ctx: (
            query.where(Product.price >= float(ctx.args['min_price']))
            if 'min_price' in ctx.args else query
        ),
        'item': lambda query, ctx: query.where(Product.in_stock.is_(True)),
    },
)
```

---

## Authentication & Method Decorators (`method_decorators`)

Attach decorators globally on `Api(...)` or per resource via `method_decorators`. Works seamlessly with `flask_user_auth`:

```python
from flask_user_auth import admin_required, auth_required, token_required

# 1. Single decorator applied to ALL actions ('list', 'item', 'create', 'update', 'delete')
api.create_resources(
    model=Product,
    schema=ProductSchema,
    method_decorators=auth_required,
)

# 2. Action-specific mapping (public read, authenticated create/update, admin delete)
api.create_resources(
    model=Product,
    schema=ProductSchema,
    method_decorators={
        'create': auth_required,
        'update': auth_required,
        'delete': admin_required,
    },
)

# 3. Combining shared ('all') and action-specific decorators
api.create_resources(
    model=Product,
    schema=ProductSchema,
    method_decorators=[
        token_required,
        {'delete': admin_required},
    ],
)
```

---

## Nested Subresources & Singletons (`create_subresource`)

Mount child resources under a parent resource with automatic parent lookup (`404` if the parent does not exist) and automatic query/create scoping:

```python
category_api = api.create_resources(model=Category, schema=CategorySchema)

# Mounts:
# GET, POST /api/categories/<int:cat_id>/products
# GET, PUT, PATCH, DELETE /api/categories/<int:cat_id>/products/<int:pk>
category_products_api = category_api.create_subresource(
    'products',
    'category_products',
    model=Product,
    schema=ProductSchema,
    route_params={'cat_id': {'model': Category, 'type': int, 'field': 'id'}},
    resolve_params={'cat_id': 'category'},
)
```

- `route_params`: Looks up `Category` where `Category.id == cat_id` before running the handler (returns `404` if missing).
- `resolve_params`: Scopes `GET` queries to `Product.category == <resolved Category>` and automatically attaches `instance.category = <resolved Category>` on `POST`.
- Pass `singleton=True` for 1-to-1 child resources (e.g., `/api/users/<int:user_id>/profile`), which serves `GET`, `POST`, `PUT`, `PATCH`, `DELETE` directly at `/profile` without a child `<pk>` and returns `409 Conflict` if a second record is POSTed.

---

## Custom Endpoints (`@resource.route`)

Add custom collection-level or detail-level endpoints to any resource. The handler receives `(schema, ctx)` where `ctx` is a [`RouteContext`](file:///c:/Users/danni/OneDrive/Desktop/project-3/foodmart/packages/flask-api/flask_api/routes.py#L14-L49):

- `ctx.value`: The resolved target record (when `detail=True` or `route_params` is provided), also accessible by lowercase model name (e.g. `ctx.product`).
- `ctx.data`: Payload validated and loaded through `schema` (for `POST`/`PUT`/`PATCH` when `validate=True`).
- `ctx.json`: Raw request JSON dict.
- `ctx.args`: Query string parameters (`request.args`).

```python
# Detail endpoint: POST /api/products/<int:pk>/discount
@product_api.route('discount', methods=['POST'], detail=True, schemas=DiscountSchema)
def discount_product(schema, ctx):
    product = ctx.product              # same as ctx.value
    percent = ctx.data['percent']      # validated via DiscountSchema
    product.price = round(product.price * (1 - percent / 100), 2)
    db.session.commit()
    return ctx.parent.schema.dump(product)


# Collection endpoint: POST /api/products/bulk-restock
@product_api.route('bulk-restock', methods=['POST'], validate=False)
def bulk_restock(schema, ctx):
    db.session.execute(sa.update(Product).values(in_stock=True))
    db.session.commit()
    return {'message': 'All products restocked'}, 200
```

---

## Error Formatting & View Overrides

### Custom Error Payloads (`errors`)

Customize error responses by status code (`404`, `400`, `409`, `500`) or exception class (`ValidationError`, `SQLAlchemyError`):

```python
from marshmallow import ValidationError

api = Api(
    api_bp,
    db=db,
    errors={
        404: lambda msg: {'status': 'error', 'message': msg},
        ValidationError: lambda err: ({'status': 'fail', 'errors': err.messages}, 422),
    },
)
```

### Overriding Default CRUD Actions (`view_overrides`)

Replace any built-in handler (`list`, `item`/`retrieve`, `create`, `update`, `patch`/`patch_update`, `delete`) with a custom function `fn(view, pk)`:

```python
product_api = api.create_resources(
    model=Product,
    schema=ProductSchema,
    response_code={'delete': 204},
    view_overrides={
        'delete': lambda view, pk: (
            # Soft-delete example
            setattr(view._get_item(pk), 'in_stock', False) or db.session.commit() or ({'message': 'Archived'}, 200)
        ),
    },
)
```

---

## Stable Error Contract & Request IDs

Every default error keeps the historical top-level `message` and adds a machine-readable `error` object. Request IDs are assigned (or propagated from `X-Request-ID`) automatically and echoed back on every response.

```json
{
  "message": "Record not found",
  "error": {
    "code": "not_found",
    "request_id": "4f1c9a0e8c2b4d6fa1e3b7c5d9f0a2b4",
    "documentation_url": "https://api.example.com/errors#not_found"
  }
}
```

Validation failures include the field messages in `error.details`:

```json
{
  "message": "Validation error",
  "error": {
    "code": "validation_error",
    "details": {"price": ["Missing data for required field."]},
    "request_id": "4f1c9a0e8c2b4d6fa1e3b7c5d9f0a2b4"
  }
}
```

Stable codes include `bad_request`, `validation_error`, `not_found`, `conflict` (including `IntegrityError` → `409`), `precondition_failed`, `precondition_required`, `idempotency_key_reuse`, `idempotency_in_progress`, `unpaginated_response_too_large`, and `internal_error`.

Set `error_docs_url` to add `documentation_url` links:

```python
api.create_resource(model=Product, schema=ProductSchema, error_docs_url='https://api.example.com/errors')
```

Raise `ApiError` from hooks, overrides, or custom routes to produce the same contract with custom status, code, details, and headers:

```python
from flask_api import ApiError

raise ApiError(409, 'out_of_stock', 'Only 2 units remain', details={'available': 2})
```

Custom handlers registered in `errors={...}` still take precedence.

---

## Conditional Requests (ETag / If-Match)

Detail reads and write responses include an `ETag` derived from the serialized representation (including eager-loaded relations).

- `GET /api/products/1` → `200` with `ETag: "…"`
- `GET /api/products/1` with `If-None-Match: "…"` → `304 Not Modified` (empty body)
- `PUT`/`PATCH`/`DELETE` with a stale `If-Match` → `412 Precondition Failed`
- Any existing record matches `If-Match: *`

Enable strict optimistic concurrency by requiring the header on writes:

```python
api.create_resource(
    model=Product,
    schema=ProductSchema,
    require_if_match=True,  # missing If-Match -> 428 Precondition Required
)
```

ETags are disabled with `etag_enabled=False`.

---

## Idempotent Creates

Supply an idempotency store to make `POST` retries safe. Requests are fingerprinted (method, path, query string, raw body, and caller identity). A successful response is cached for `idempotency_ttl` seconds and replayed with `Idempotent-Replayed: true`.

```python
from flask_api import Api, MemoryIdempotencyStore, RedisIdempotencyStore

# Single process / tests
store = MemoryIdempotencyStore()

# Multi-worker deployments
store = RedisIdempotencyStore(url='redis://localhost:6379/1')

api = Api(api_bp, db=db, idempotency_store=store)
```

```bash
curl -X POST https://api.example.com/products \
  -H "Idempotency-Key: 6f1c1c2e-1f2b-4c3d-9e4f-1a2b3c4d5e6f" \
  -H "Content-Type: application/json" \
  -d '{"name": "Keyboard", "price": 49.9}'
```

- Reusing a key with a different payload → `409 idempotency_key_reuse`
- Retrying while the first request is still running → `409 idempotency_in_progress` with `Retry-After: 1`
- Non-2xx outcomes are not cached, so the key can be retried
- Keys are scoped per user by default; supply `idempotency_scope=lambda: tenant_id` to scope differently

---

## Action Aliases & Custom Endpoint Names (`endpoint_names`)

### 1. Flexible Action & HTTP-Verb Aliases (Case-Insensitive)

Across `method_decorators`, `view_overrides`, `response_code`, `_query`, `endpoint_names`, and `docs['resources']`, you can use either **REST action names** or **HTTP verb names** (case-insensitive):

| Canonical Action | HTTP Route | Accepted Aliases (Case-Insensitive) |
| :--- | :--- | :--- |
| **`list`** | `GET /<resource>` | `'list'`, `'get_list'`, `'get_collection'`, `'collection'`, `'GET_LIST'`, `'GET_COLLECTION'` |
| **`item`** | `GET /<resource>/<pk>` | `'item'`, `'retrieve'`, `'detail'`, `'show'`, `'get'`, `'get_item'`, `'get_one'`, `'get_single'`, `'GET'` |
| **`create`** | `POST /<resource>` | `'create'`, `'post'`, `'POST'` |
| **`update`** | `PUT /<resource>/<pk>` | `'update'`, `'put'`, `'PUT'` *(also acts as fallback for `PATCH` if `patch` is not specified separately)* |
| **`patch`** | `PATCH /<resource>/<pk>` | `'patch'`, `'patch_update'`, `'partial_update'`, `'PATCH'` |
| **`delete`** | `DELETE /<resource>/<pk>` | `'delete'`, `'destroy'`, `'DELETE'` |

### 2. Custom Flask Endpoint Names (`{resource_name}_{endpoint_name}`)

By default, each resource registers distinct Flask route endpoints named `{resource_name}_{action}` (`products_list`, `products_retrieve`, `products_create`, `products_update`, `products_patch`, `products_delete`).

Pass `endpoint_names={...}` (globally on `Api(...)` or per resource) to customize the endpoint suffix for `url_for()` and OpenAPI `operationId`:

```python
product_api = api.create_resources(
    model=Product,
    schema=ProductSchema,
    resource_name='products',
    endpoint_names={
        'list': 'collection',
        'retrieve': 'retrieve',
        'create': 'create',
        'update': 'update',
        'patch': 'patch_update',
        'delete': 'delete',
    },
)
# Registers Flask endpoints:
# - url_for('api.products_collection')            -> GET    /api/products
# - url_for('api.products_create')                -> POST   /api/products
# - url_for('api.products_retrieve', pk=1)        -> GET    /api/products/1
# - url_for('api.products_update', pk=1)          -> PUT    /api/products/1
# - url_for('api.products_patch_update', pk=1)    -> PATCH  /api/products/1
# - url_for('api.products_delete', pk=1)          -> DELETE /api/products/1
```

### 3. Exposing Specific Methods (`methods=[...]`)

By default, all CRUD actions (`list`, `create`, `retrieve`, `update`, `patch`, `delete`) are enabled. Pass `methods=[...]` to expose only a specific whitelist of actions or HTTP verbs:

```python
# Expose only read-only endpoints (GET /categories and GET /categories/<pk>)
category_api = api.create_resources(
    model=Category,
    schema=CategorySchema,
    methods=['GET'],  # or ['list', 'retrieve']
)

# Expose only list and create (no detail route /<pk> mounted)
category_api = api.create_resources(
    model=Category,
    schema=CategorySchema,
    methods=['GET_LIST', 'POST'],  # or ['list', 'create']
)

# Any unexposed method returns 405 Method Not Allowed and is excluded from Swagger/OpenAPI docs
```

---

## Auto-Generated API Documentation (Swagger UI & ReDoc)

Enable interactive **Swagger UI**, **ReDoc**, and **OpenAPI 3.0.3 (`openapi.json`)** generation directly on `Api(...)` or `api.init_app(...)`:

```python
api = Api(
    api_bp,
    db=db,
    enable_docs=True,
    doc=['swagger', 'redoc'],              # 'swagger', 'redoc', or ['swagger', 'redoc'] (alias: doc_ui=...)
    docs={
        'title': 'FoodMart API',
        'version': '1.0.0',
        'description': 'REST API for the FoodMart grocery marketplace.',
        'openapi_url': '/openapi.json',    # Mounted on api_bp -> /api/openapi.json
        'swagger_url': '/docs',            # Mounted on api_bp -> /api/docs
        'redoc_url': '/redoc',             # Mounted on api_bp -> /api/redoc
    },
)
```

### Resource-Level & Action-Level `docs={...}`

Schemas, query parameters (`filter_fields`, `search_fields`, `sort_fields`, pagination), path parameters, and authentication badges (`method_decorators`) are introspected automatically. You can customize tags, descriptions, and per-action metadata via `docs={...}`:

```python
product_api = api.create_resources(
    model=Product,
    schema=ProductSchema,
    filter_fields=['in_stock', 'category_id'],
    search_fields=['name'],
    sort_fields=['id', 'name', 'price'],
    docs={
        'tag': 'Products',
        'description': 'Manage store catalog products.',
        'resources': {
            'list': {
                'summary': 'List catalog products',
                'description': 'Returns a paginated list of products with filtering and search.',
            },
            'retrieve': {
                'summary': 'Get product details',
            },
            'create': {
                'tag': 'Admin - Products',
                'summary': 'Create a new product',
            },
            'patch_update': {
                'summary': 'Partially update a product',
            },
            'delete': {
                'summary': 'Delete a product',
            },
        },
    },
)
```

Custom `@resource.route(...)` endpoints are also included automatically and accept `docs={...}`:

```python
@product_api.route(
    'discount',
    methods=['POST'],
    detail=True,
    schemas=DiscountSchema,
    docs={
        'tag': 'Products',
        'summary': 'Apply percentage discount to a product',
    },
)
def discount_product(schema, ctx):
    ...
```

