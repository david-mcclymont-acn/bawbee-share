"""bank connections and sync runs

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21 12:20:00.540644
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Resumable: a first run of this migration (before env.py made migrations transactional)
    # could stop half way on a database with data, leaving some of these tables made and a
    # temporary copy of bank_transaction behind. Whatever already exists is kept.
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "_alembic_tmp_bank_transaction" in tables:
        op.drop_table("_alembic_tmp_bank_transaction")

    def _indexes(table: str) -> set[str]:
        return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}

    def _columns(table: str) -> set[str]:
        return {c["name"] for c in sa.inspect(bind).get_columns(table)}

    if "bank_connection" not in tables:
        op.create_table(
            "bank_connection",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_user_id", sa.Integer(), nullable=False),
            sa.Column(
                "provider",
                sa.Enum(
                    "enable_banking",
                    "starling_pat",
                    "demo",
                    name="bank_provider",
                    native_enum=False,
                    create_constraint=True,
                    length=32,
                ),
                nullable=False,
            ),
            sa.Column("bank_name", sa.String(length=100), nullable=False),
            sa.Column("bank_country", sa.String(length=2), nullable=False),
            sa.Column("secret_encrypted", sa.Text(), nullable=False),
            sa.Column("consent_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "status",
                sa.Enum(
                    "active",
                    "expired",
                    "revoked",
                    "error",
                    name="bank_connection_status",
                    native_enum=False,
                    create_constraint=True,
                    length=32,
                ),
                nullable=False,
            ),
            sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint(
                "provider IN ('enable_banking', 'starling_pat', 'demo')", name="bank_provider"
            ),
            sa.CheckConstraint(
                "provider IN ('enable_banking', 'starling_pat', 'demo')",
                name=op.f("ck_bank_connection_bank_provider"),
            ),
            sa.CheckConstraint(
                "status IN ('active', 'expired', 'revoked', 'error')", name="bank_connection_status"
            ),
            sa.CheckConstraint(
                "status IN ('active', 'expired', 'revoked', 'error')",
                name=op.f("ck_bank_connection_bank_connection_status"),
            ),
            sa.ForeignKeyConstraint(
                ["owner_user_id"], ["user.id"], name=op.f("fk_bank_connection_owner_user_id_user")
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_connection")),
        )
    if "bank_account_link" not in tables:
        op.create_table(
            "bank_account_link",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("connection_id", sa.Integer(), nullable=False),
            sa.Column("external_uid", sa.String(length=128), nullable=False),
            sa.Column("identification_hash", sa.String(length=128), nullable=True),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("detail", sa.String(length=100), nullable=True),
            sa.Column("currency", sa.String(length=3), nullable=False),
            sa.Column("account_id", sa.Integer(), nullable=True),
            sa.Column("synced_from", sa.Date(), nullable=True),
            sa.Column("synced_through", sa.Date(), nullable=True),
            sa.Column("balance_pence", sa.Integer(), nullable=True),
            sa.Column("balance_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["account_id"], ["account.id"], name=op.f("fk_bank_account_link_account_id_account")
            ),
            sa.ForeignKeyConstraint(
                ["connection_id"],
                ["bank_connection.id"],
                name=op.f("fk_bank_account_link_connection_id_bank_connection"),
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_account_link")),
            sa.UniqueConstraint(
                "connection_id",
                "external_uid",
                name=op.f("uq_bank_account_link_connection_id_external_uid"),
            ),
        )
    if "ix_bank_account_link_identification_hash" not in _indexes("bank_account_link"):
        with op.batch_alter_table("bank_account_link", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_bank_account_link_identification_hash"),
                ["identification_hash"],
                unique=False,
            )

    if "bank_link_flow" not in tables:
        op.create_table(
            "bank_link_flow",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("bank_name", sa.String(length=100), nullable=False),
            sa.Column("bank_country", sa.String(length=2), nullable=False),
            sa.Column("relink_connection_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["relink_connection_id"],
                ["bank_connection.id"],
                name=op.f("fk_bank_link_flow_relink_connection_id_bank_connection"),
            ),
            sa.ForeignKeyConstraint(
                ["user_id"], ["user.id"], name=op.f("fk_bank_link_flow_user_id_user")
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_link_flow")),
            sa.UniqueConstraint("state", name=op.f("uq_bank_link_flow_state")),
        )
    if "sync_run" not in tables:
        op.create_table(
            "sync_run",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("connection_id", sa.Integer(), nullable=False),
            sa.Column("trigger", sa.String(length=16), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("rows_new", sa.Integer(), nullable=False),
            sa.Column("rows_updated", sa.Integer(), nullable=False),
            sa.Column("rows_removed", sa.Integer(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(
                ["connection_id"],
                ["bank_connection.id"],
                name=op.f("fk_sync_run_connection_id_bank_connection"),
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_run")),
        )
    if "ix_sync_run_connection_id" not in _indexes("sync_run"):
        with op.batch_alter_table("sync_run", schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f("ix_sync_run_connection_id"), ["connection_id"], unique=False
            )

    if "confirmed_at" not in _columns("auth_session"):
        with op.batch_alter_table("auth_session", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)
            )

    if "connection_id" not in _columns("bank_transaction"):
        with op.batch_alter_table("bank_transaction", schema=None) as batch_op:
            batch_op.add_column(sa.Column("connection_id", sa.Integer(), nullable=True))
            batch_op.create_index(
                batch_op.f("ix_bank_transaction_connection_id"), ["connection_id"], unique=False
            )
            batch_op.create_foreign_key(
                batch_op.f("fk_bank_transaction_connection_id_bank_connection"),
                "bank_connection",
                ["connection_id"],
                ["id"],
            )


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("bank_transaction", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_bank_transaction_connection_id_bank_connection"), type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_bank_transaction_connection_id"))
        batch_op.drop_column("connection_id")

    with op.batch_alter_table("auth_session", schema=None) as batch_op:
        batch_op.drop_column("confirmed_at")

    with op.batch_alter_table("sync_run", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sync_run_connection_id"))

    op.drop_table("sync_run")
    op.drop_table("bank_link_flow")
    with op.batch_alter_table("bank_account_link", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_bank_account_link_identification_hash"))

    op.drop_table("bank_account_link")
    op.drop_table("bank_connection")
    # ### end Alembic commands ###
