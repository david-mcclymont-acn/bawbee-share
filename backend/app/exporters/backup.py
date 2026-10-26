"""JSON backup of every table (PLAN.md §11.3 Settings → Export).

Secrets are left out: password hashes, sessions, sign-in flows and login attempts, and the
bank session ids and tokens (encrypted or not, a backup has no need of them; re-link instead),
and Home Assistant token hashes (make a new token instead).
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

import app.domain.models  # noqa: F401  (registers every table)
from app import __version__
from app.db import Base, utcnow

SKIP_TABLES = {"auth_session", "auth_flow", "login_attempt", "bank_link_flow"}
SKIP_COLUMNS = {
    ("user", "password_hash"),
    ("bank_connection", "secret_encrypted"),
    ("ha_token", "token_hash"),
}


def _plain(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def backup(session: Session) -> dict[str, Any]:
    revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in Base.metadata.sorted_tables:
        if table.name in SKIP_TABLES:
            continue
        columns = [c for c in table.columns if (table.name, c.name) not in SKIP_COLUMNS]
        rows = session.execute(select(*columns).order_by(*table.primary_key.columns)).all()
        tables[table.name] = [
            {c.name: _plain(v) for c, v in zip(columns, row, strict=True)} for row in rows
        ]
    return {
        "app": "bawbee",
        "app_version": __version__,
        "schema_revision": revision,
        "exported_at": utcnow().isoformat(),
        "excluded": sorted(SKIP_TABLES) + [f"{t}.{c}" for t, c in sorted(SKIP_COLUMNS)],
        "tables": tables,
    }
