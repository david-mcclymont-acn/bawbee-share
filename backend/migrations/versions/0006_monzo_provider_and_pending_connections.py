"""Monzo as a bank provider, and connections waiting for approval

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22 13:30:00

Monzo isn't on Enable Banking, so it gets its own provider (the Monzo Developer API). A Monzo
sign-in can't read anything until it's approved in the Monzo app: "pending" until then.
Enum checks aren't seen by autogenerate, so bank_connection is rebuilt with the new checks.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_PROVIDERS = ("enable_banking", "starling_pat", "demo")
NEW_PROVIDERS = ("enable_banking", "starling_pat", "monzo", "demo")
OLD_STATUSES = ("active", "expired", "revoked", "error")
NEW_STATUSES = ("pending", "active", "expired", "revoked", "error")


def _enum(values: tuple[str, ...], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    with op.batch_alter_table("bank_connection", schema=None, recreate="always") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=_enum(OLD_PROVIDERS, "bank_provider"),
            type_=_enum(NEW_PROVIDERS, "bank_provider"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "status",
            existing_type=_enum(OLD_STATUSES, "bank_connection_status"),
            type_=_enum(NEW_STATUSES, "bank_connection_status"),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.execute("UPDATE bank_connection SET status = 'error' WHERE status = 'pending'")
    op.execute(
        "DELETE FROM bank_account_link WHERE connection_id IN "
        "(SELECT id FROM bank_connection WHERE provider = 'monzo')"
    )
    op.execute(
        "UPDATE bank_transaction SET connection_id = NULL WHERE connection_id IN "
        "(SELECT id FROM bank_connection WHERE provider = 'monzo')"
    )
    op.execute(
        "DELETE FROM sync_run WHERE connection_id IN "
        "(SELECT id FROM bank_connection WHERE provider = 'monzo')"
    )
    op.execute(
        "UPDATE bank_link_flow SET relink_connection_id = NULL WHERE relink_connection_id IN "
        "(SELECT id FROM bank_connection WHERE provider = 'monzo')"
    )
    op.execute("DELETE FROM bank_connection WHERE provider = 'monzo'")
    with op.batch_alter_table("bank_connection", schema=None, recreate="always") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=_enum(NEW_PROVIDERS, "bank_provider"),
            type_=_enum(OLD_PROVIDERS, "bank_provider"),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "status",
            existing_type=_enum(NEW_STATUSES, "bank_connection_status"),
            type_=_enum(OLD_STATUSES, "bank_connection_status"),
            existing_nullable=False,
        )
