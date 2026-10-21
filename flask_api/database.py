from dataclasses import dataclass
import math
from typing import Any

from flask import current_app
import sqlalchemy as sa


@dataclass
class PaginationResult:
    """Lightweight pagination result compatible with Flask-SQLAlchemy's Pagination."""

    items: list[Any]
    page: int
    per_page: int
    total: int

    @property
    def pages(self) -> int:
        if self.per_page <= 0 or self.total <= 0:
            return 0
        return math.ceil(self.total / self.per_page)


def get_db(explicit_db=None, app=None):
    """Return the registered Flask-SQLAlchemy extension instance, if available."""
    if explicit_db is not None:
        return explicit_db
    app = app or current_app
    sa_ext = getattr(app, 'extensions', {}).get('sqlalchemy')
    if sa_ext is not None:
        if hasattr(sa_ext, 'db'):
            return sa_ext.db
        return sa_ext
    return None


def get_session(explicit_db=None, app=None):
    """Resolve the active SQLAlchemy 2.0 ``Session``."""
    db_ext = get_db(explicit_db=explicit_db, app=app)
    if db_ext is not None and hasattr(db_ext, 'session'):
        return db_ext.session
    raise RuntimeError(
        'SQLAlchemy extension is not registered on the current Flask application '
        'and no explicit db instance was provided.'
    )


def paginate_statement(stmt, *, page: int = 1, per_page: int = 10,
                       error_out: bool = False, explicit_db=None, app=None):
    """Paginate a SQLAlchemy 2.0 ``Select`` statement.

    Uses Flask-SQLAlchemy's ``db.paginate`` when available, otherwise falls
    back to executing a count + limit/offset query directly on the session.
    """
    db_ext = get_db(explicit_db=explicit_db, app=app)
    if db_ext is not None and hasattr(db_ext, 'paginate'):
        return db_ext.paginate(
            stmt, page=page, per_page=per_page, error_out=error_out
        )

    session = get_session(explicit_db=explicit_db, app=app)
    count_stmt = sa.select(sa.func.count()).select_from(stmt.order_by(None).subquery())
    total = int(session.scalar(count_stmt) or 0)
    offset = max(page - 1, 0) * per_page
    items = list(session.scalars(stmt.limit(per_page).offset(offset)).all())
    return PaginationResult(
        items=items,
        page=page,
        per_page=per_page,
        total=total,
    )
