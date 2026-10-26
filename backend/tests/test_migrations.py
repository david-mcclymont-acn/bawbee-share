import json
from pathlib import Path

from alembic import command
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import Base, make_engine
from app.domain.models import Account
from app.importers.seed import import_seed
from tests.conftest import AS_OF, SEED_PATH, alembic_config


def test_migrations_match_the_models(tmp_path: Path) -> None:
    """Upgrade an empty database, then confirm the models need no further migration."""
    config = alembic_config(f"sqlite:///{tmp_path / 'm.db'}")
    command.upgrade(config, "head")
    command.check(config)  # raises if autogenerate would produce changes
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def _phase3_database(path: Path) -> str:
    """A database as Phase 3 left it in use: imported transactions that other rows point at."""
    url = f"sqlite:///{path}"
    command.upgrade(alembic_config(url), "0003")
    engine = make_engine(url)
    with Session(engine) as session:
        import_seed(session, json.loads(SEED_PATH.read_text(encoding="utf-8")), AS_OF)
        joint = session.scalars(select(Account).where(Account.key == "joint_billing")).one()
        session.execute(
            text(
                "INSERT INTO bank_transaction (account_id, external_id, booked_date, status,"
                " amount_pence, description, merchant_normalised, raw_json, created_at)"
                " VALUES (:a, 'x1', '2026-11-01', 'booked', -10850, 'SKY', 'SKY', '{}',"
                " '2026-11-01')"
            ),
            {"a": joint.id},
        )
        txn = session.execute(text("SELECT id FROM bank_transaction")).scalar()
        session.execute(
            text(
                "INSERT INTO inbox_item (type, status, dedupe_key, transaction_id, payload_json,"
                " created_at) VALUES ('price_change', 'open', 'k1', :t, '{}', '2026-11-01')"
            ),
            {"t": txn},
        )
        session.commit()
    engine.dispose()
    return url


def _after(url: str) -> tuple[str, int, int, list[tuple[object, ...]]]:
    engine = make_engine(url)
    with engine.connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        rows = conn.execute(text("SELECT count(*) FROM bank_transaction")).scalar()
        linked = conn.execute(
            text("SELECT count(*) FROM inbox_item WHERE transaction_id IS NOT NULL")
        ).scalar()
        broken = [tuple(r) for r in conn.exec_driver_sql("PRAGMA foreign_key_check")]
    engine.dispose()
    return str(version), int(rows or 0), int(linked or 0), broken


def test_upgrading_a_database_with_data_keeps_every_reference(tmp_path: Path) -> None:
    """Migrations that rebuild a table must work when other rows point at it (Phase 4 bug)."""
    url = _phase3_database(tmp_path / "used.db")
    command.upgrade(alembic_config(url), "head")
    version, rows, linked, broken = _after(url)
    assert (rows, linked, broken) == (1, 1, [])
    assert version >= "0004"


def test_0004_finishes_after_a_half_done_first_run(tmp_path: Path) -> None:
    """The first, non-transactional run of 0004 could stop half way; the next start recovers."""
    url = _phase3_database(tmp_path / "half.db")
    engine = make_engine(url)
    with engine.begin() as conn:
        for name in ("bank_connection", "bank_account_link", "bank_link_flow", "sync_run"):
            Base.metadata.tables[name].create(conn)
        conn.execute(text("ALTER TABLE auth_session ADD COLUMN confirmed_at DATETIME"))
        conn.execute(text("CREATE TABLE _alembic_tmp_bank_transaction (id INTEGER)"))
    engine.dispose()

    command.upgrade(alembic_config(url), "head")
    version, rows, linked, broken = _after(url)
    assert (rows, linked, broken) == (1, 1, [])
    assert version >= "0004"
    command.check(alembic_config(url))
