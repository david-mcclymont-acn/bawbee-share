"""Database engine and sessions. SQLite (WAL) by default; nothing here is SQLite-only."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

# Named constraints keep Alembic migrations deterministic (and SQLite batch mode working).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(UTC)


def must_get[T](session: Session, model: type[T], ident: object) -> T:
    """Load a row that must exist (e.g. one this code just referenced)."""
    obj = session.get(model, ident)
    if obj is None:
        raise LookupError(f"No {model.__name__} with id {ident!r}")
    return obj


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    if parsed.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


_engine: Engine | None = None
_sessions: sessionmaker[Session] | None = None


def _sessionmaker() -> sessionmaker[Session]:
    global _engine, _sessions
    if _sessions is None:
        _engine = make_engine(get_settings().database_url)
        _sessions = sessionmaker(_engine, expire_on_commit=False)
    return _sessions


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with _sessionmaker()() as session:
        yield session
