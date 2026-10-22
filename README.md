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
| `schema` | *Required* | Marshmallow `Schema` class or instance. |
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
| `filter_fields` | `()` | Whitelist of model column names allowed for exact-match filtering (`?field=value`). |
| `filter_types` | `{}` | Mapping of `{field: callable}` type casters for filter values (e.g., `{'in_stock': bool, 'category_id': int}`). |
| `search_fields` | `()` | Model string column names searched with case-insensitive `ILIKE` via `?search=term`. |
| `sort_fields` | `()` | Whitelist of model columns allowed for `?sort=field` (allows any model column if empty). |
| `default_sort_field` | `''` | Default column used to sort list queries when `?sort=` is omitted. |
| `sort_order` | `'asc'` | Default sort direction (`'asc'` or `'desc'`), overridden via `?sort_order=desc`. |
| `eager_loads` | `()` | Eager loading specifications applied to list and detail queries. |
| `hooks` | `{}` | Lifecycle hooks (`before_save`, `after_save`, `before_update`, `after_update`, `before_delete`, `after_delete`). |
| `_query` | `{}` | Per-action query modifiers (`{'list': fn, 'item': fn}`). |
| `method_decorators` | `()` | Decorators applied to all or specific CRUD actions. |
| `view_overrides` | `{}` | Custom handler functions overriding `list`, `item`, `create`, `update`, or `delete`. |
| `response_code` | `{}` | Override default HTTP status codes per action (`{'delete': 204}` returns an empty 204 body). |
| `errors` | `{}` | Custom error formatters keyed by HTTP status code (`404`) or exception class (`ValidationError`). |

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

Run custom logic before or after `create`, `update`, and `delete` operations. Both `before_save` and `before_update` run **after** `schema.load()` has validated the payload and hydrated `item`, and every hook receives `ctx` ([`QueryContext`](file:///c:/Users/danni/OneDrive/Desktop/project-3/foodmart/packages/flask-api/flask_api/query.py#L15-L78), exposing `ctx.args`, `ctx.pk`, `ctx.view`, `ctx.model`, `ctx.schema`, and resolved parent route params):

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

Replace any built-in handler (`list`, `item`, `create`, `update`, `delete`) with a custom function `fn(view, pk)`:

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
