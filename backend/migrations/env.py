from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine, make_url

import app.domain.models  # noqa: F401  (registers the tables on Base.metadata)
from app.config import get_settings
from app.db import Base, make_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


class BrokenReferences(RuntimeError):
    pass


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _configure(**kwargs: object) -> None:
    # Batch mode lets SQLite "alter" tables by copying them.
    context.configure(target_metadata=target_metadata, render_as_batch=True, **kwargs)  # type: ignore[arg-type]


def run_migrations_offline() -> None:
    _configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _check_references(connection: Connection) -> None:
    """With foreign keys off for the copy-and-rename, prove nothing was left dangling."""
    if connection.dialect.name != "sqlite":
        return
    broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if broken:
        raise BrokenReferences(f"A migration left rows pointing at nothing: {broken[:5]}")


def _run(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
        _check_references(connection)


def _migration_engine(url: str) -> Engine:
    """The app's engine, set up for migrating SQLite safely.

    - Batch mode rebuilds a table by copying it and dropping the original. With foreign keys
      enforced, dropping a table that other rows point at fails, so they're off while
      migrating (SQLite can only change that outside a transaction, hence at connect).
      _check_references then proves every reference still holds before committing.
    - Python's sqlite3 commits schema changes as it goes; letting SQLAlchemy issue BEGIN
      itself makes the whole run one transaction, so a failure leaves nothing half done.
    """
    engine = make_engine(url)
    if make_url(url).get_backend_name() != "sqlite":
        return engine

    @event.listens_for(engine, "connect")
    def _connect(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN")

    return engine


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = _migration_engine(_url())
    try:
        with engine.connect() as conn:
            _run(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
