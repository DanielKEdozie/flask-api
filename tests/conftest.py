import pytest
import sqlalchemy as sa
from flask import Blueprint, Flask
from flask_sqlalchemy import SQLAlchemy
from marshmallow import Schema, fields
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from flask_api import Api, MemoryIdempotencyStore


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


class Widget(db.Model):
    __tablename__ = 'widgets'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    sku: Mapped[str] = mapped_column(sa.String(40), nullable=False, unique=True)
    price: Mapped[float] = mapped_column(sa.Float, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.String(255))


class WidgetSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str(required=True)
    sku = fields.Str(required=True)
    price = fields.Float(required=True)
    description = fields.Str(allow_none=True, load_default=None)


class Gadget(db.Model):
    __tablename__ = 'gadgets'

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    price: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)


class GadgetSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str(required=True)
    price = fields.Float(required=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', TESTING=True)
    db.init_app(app)
    with app.app_context():
        db.create_all()

    api_bp = Blueprint('api', __name__, url_prefix='/api')
    api = Api(api_bp, db=db, idempotency_store=MemoryIdempotencyStore())

    api.create_resource(
        model=Widget,
        schema=WidgetSchema,
        resource_name='widgets',
        filter_fields=('name',),
        search_fields=('name',),
        sort_fields=('id', 'name'),
        default_sort_field='id',
    )
    api.create_resource(
        model=Gadget,
        schema=GadgetSchema,
        resource_name='gadgets',
        require_if_match=True,
    )
    api.create_resource(
        model=Widget,
        schema=WidgetSchema,
        resource_name='limited-widgets',
        max_unpaginated=2,
    )

    app.register_blueprint(api_bp)
    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def widget_payload(**overrides):
    payload = {'name': 'Widget', 'sku': 'SKU-1', 'price': 10.0}
    payload.update(overrides)
    return payload
