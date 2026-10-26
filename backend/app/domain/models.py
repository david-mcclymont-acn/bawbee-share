"""Domain model (PLAN.md §6). Money is integer pence; dates are ISO; timestamps are UTC.

Tables for later phases (bank sync, mortgage, childcare, forecast, payday runs) arrive with
the phases that use them.
"""

from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow

AccountRole = Literal[
    "salary_hub", "subscriptions", "joint_bills", "savings", "investment", "dormant"
]
LineKind = Literal["bill", "allocation", "transfer"]
LineStatus = Literal["active", "cancelled"]
Frequency = Literal["monthly", "annual", "quarterly", "weekly"]
VersionType = Literal["live", "what_if", "period"]
VersionStatus = Literal["active", "draft", "promoted", "archived"]
OverrideAction = Literal["modify", "remove", "add"]
FollowUpReason = Literal["cancel", "check_price", "check_account", "check", "other"]
FollowUpStatus = Literal["open", "done", "dismissed"]
PensionScheme = Literal["nhs_scotland", "workplace_pct", "none"]
PayDayRuleName = Literal["last_working_day", "last_thursday", "fixed_day"]


def _enum(*values: str, name: str) -> Enum:
    # Stored as VARCHAR + CHECK, so the schema ports to MariaDB/Postgres unchanged.
    return Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Person(Base):
    __tablename__ = "person"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(100))


class User(Timestamped, Base):
    """A login (PLAN.md decision 27): local password by default, OIDC optional."""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    person_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"))
    email: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Account(Base):
    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    # None means the account is held jointly.
    owner_person_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"))
    provider: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(
        _enum(
            "salary_hub",
            "subscriptions",
            "joint_bills",
            "savings",
            "investment",
            "dormant",
            name="account_role",
        )
    )
    notes: Mapped[str | None] = mapped_column(Text)
    opening_balance_pence: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")

    owner: Mapped[Person | None] = relationship()


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    colour: Mapped[str | None] = mapped_column(String(32))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))


class BudgetVersion(Timestamped, Base):
    """The live budget, a what-if (overrides only), or a frozen period (PLAN.md §5.1)."""

    __tablename__ = "budget_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    type: Mapped[str] = mapped_column(_enum("live", "what_if", "period", name="version_type"))
    base_version_id: Mapped[int | None] = mapped_column(ForeignKey("budget_version.id"))
    planned_start: Mapped[date | None] = mapped_column(Date)
    # Live: the date it became live. Period: the dates it covered.
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        _enum("active", "draft", "promoted", "archived", name="version_status")
    )
    # Sparse settings: split_basis, fixed_shares, rounding_step_pence, buffer_pence,
    # extra_lines_mode. Unset keys inherit from the base version.
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Periods only: the resolved budget as it stood, frozen.
    snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    base: Mapped["BudgetVersion | None"] = relationship(remote_side=[id])
    overrides: Mapped[list["Override"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class BudgetLine(Timestamped, Base):
    """A line's identity. Amounts live on BudgetLineVersion, dated."""

    __tablename__ = "budget_line"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str | None] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(_enum("bill", "allocation", "transfer", name="line_kind"))
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    to_account_id: Mapped[int | None] = mapped_column(ForeignKey("account.id"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    owner_person_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(_enum("active", "cancelled", name="line_status"))
    # e.g. {"type": "PCP", "end_date": "2027-10", "end_date_confirmed": false, ...}
    agreement_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Set while the line exists only as an "add" override in that what-if.
    scope_version_id: Mapped[int | None] = mapped_column(ForeignKey("budget_version.id"))

    account: Mapped[Account] = relationship(foreign_keys=[account_id])
    to_account: Mapped[Account | None] = relationship(foreign_keys=[to_account_id])
    category: Mapped[Category | None] = relationship()
    owner: Mapped[Person | None] = relationship()
    versions: Mapped[list["BudgetLineVersion"]] = relationship(
        back_populates="line", order_by="BudgetLineVersion.id"
    )


class BudgetLineVersion(Base):
    __tablename__ = "budget_line_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("budget_line.id"))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)  # exclusive
    amount_pence: Mapped[int | None] = mapped_column(Integer)
    frequency: Mapped[str] = mapped_column(
        _enum("monthly", "annual", "quarterly", "weekly", name="frequency")
    )
    day_of_month: Mapped[int | None] = mapped_column(Integer)
    renewal_date: Mapped[date | None] = mapped_column(Date)
    transfer_rule_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # A typed-in transfer amount used instead of the suggested one.
    actual_override_pence: Mapped[int | None] = mapped_column(Integer)
    # Composite bills, e.g. one pet policy per dog: [{"label": ..., "amount_pence": ...}].
    components_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    line: Mapped[BudgetLine] = relationship(back_populates="versions")


class Override(Base):
    """A sparse change in a what-if (PLAN.md §5.1)."""

    __tablename__ = "override"
    __table_args__ = (UniqueConstraint("version_id", "line_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("budget_version.id"))
    line_id: Mapped[int] = mapped_column(ForeignKey("budget_line.id"))
    action: Mapped[str] = mapped_column(_enum("modify", "remove", "add", name="override_action"))
    # modify: only the changed fields. add: every field. remove: {}.
    fields_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # The live line version this override was made against, for conflict detection.
    based_on_version_id: Mapped[int | None] = mapped_column(ForeignKey("budget_line_version.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    version: Mapped[BudgetVersion] = relationship(back_populates="overrides")
    line: Mapped[BudgetLine] = relationship()


class FollowUp(Timestamped, Base):
    __tablename__ = "follow_up"

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_line.id"))
    reason: Mapped[str] = mapped_column(
        _enum("cancel", "check_price", "check_account", "check", "other", name="follow_up_reason")
    )
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        _enum("open", "done", "dismissed", name="follow_up_status"), default="open"
    )
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    line: Mapped[BudgetLine | None] = relationship()


class IncomeSource(Base):
    __tablename__ = "income_source"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id"))
    employer: Mapped[str | None] = mapped_column(String(200))
    as_of_date: Mapped[date] = mapped_column(Date)
    gross_annual_pence: Mapped[int] = mapped_column(Integer)
    benefits_allowance_pence: Mapped[int | None] = mapped_column(Integer)
    bonus_pence: Mapped[int | None] = mapped_column(Integer)
    salary_sacrifice_pence: Mapped[int | None] = mapped_column(Integer)
    pension_scheme: Mapped[str] = mapped_column(
        _enum("nhs_scotland", "workplace_pct", "none", name="pension_scheme"), default="none"
    )
    pension_pct: Mapped[str | None] = mapped_column(String(16))  # exact decimal as text
    student_loan_plan: Mapped[str | None] = mapped_column(String(16))
    tax_code: Mapped[str | None] = mapped_column(String(16))
    hours_per_week: Mapped[str | None] = mapped_column(String(16))
    fte: Mapped[str | None] = mapped_column(String(16))

    person: Mapped[Person] = relationship()


class IncomeVersion(Base):
    """Per-version income overrides; resolved along the version chain like everything else."""

    __tablename__ = "income_version"
    __table_args__ = (UniqueConstraint("income_source_id", "version_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    income_source_id: Mapped[int] = mapped_column(ForeignKey("income_source.id"))
    version_id: Mapped[int | None] = mapped_column(ForeignKey("budget_version.id"))
    net_monthly_override_pence: Mapped[int | None] = mapped_column(Integer)
    gross_override_pence: Mapped[int | None] = mapped_column(Integer)

    income_source: Mapped[IncomeSource] = relationship()


class PayDayRule(Base):
    __tablename__ = "pay_day_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("person.id"), unique=True)
    rule: Mapped[str] = mapped_column(
        _enum("last_working_day", "last_thursday", "fixed_day", name="pay_day_rule_name")
    )
    fixed_day: Mapped[int | None] = mapped_column(Integer)
    typical_offset_days: Mapped[int] = mapped_column(Integer, default=0)
    calendar: Mapped[str] = mapped_column(String(32), default="scotland")
    learned_from_bank: Mapped[bool] = mapped_column(default=True)
    notes: Mapped[str | None] = mapped_column(Text)


class Promotion(Base):
    """A what-if promoted to live, with what's needed to undo it (PLAN.md §5.4)."""

    __tablename__ = "promotion"

    id: Mapped[int] = mapped_column(primary_key=True)
    what_if_id: Mapped[int] = mapped_column(ForeignKey("budget_version.id"))
    # Cleared when the promotion is undone and its period deleted.
    period_id: Mapped[int | None] = mapped_column(ForeignKey("budget_version.id"))
    effective_date: Mapped[date] = mapped_column(Date)
    undo_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))  # None: system
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64))
    before_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    after_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---- login (PLAN.md decision 27) -------------------------------------------------------


class AuthSession(Base):
    """A signed-in browser. The cookie holds a random token; only its hash is stored."""

    __tablename__ = "auth_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(_enum("local", "oidc", name="auth_method"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last time the password was typed again in this session, for bank linking (PLAN.md §13).
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


class LoginAttempt(Base):
    """Password attempts, for rate limiting by username and by IP."""

    __tablename__ = "login_attempt"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    ip: Mapped[str | None] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column()
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuthFlow(Base):
    """An OIDC sign-in in progress (state, PKCE verifier and nonce), used once."""

    __tablename__ = "auth_flow"

    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[str] = mapped_column(String(64), unique=True)
    code_verifier: Mapped[str] = mapped_column(String(128))
    nonce: Mapped[str] = mapped_column(String(64))
    # Set when a signed-in user is linking their OIDC identity from Settings.
    link_user_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---- payday (PLAN.md §5.7, §11.3) -----------------------------------------------------


class PaydayRun(Base):
    """One payday's checklist, saved so ticks and actual amounts are remembered."""

    __tablename__ = "payday_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The payday month: transfers made on the October payday fund November's bills.
    month: Mapped[date] = mapped_column(Date, unique=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("budget_version.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    transfers: Mapped[list["PaydayTransfer"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="PaydayTransfer.id"
    )


class PaydayTransfer(Base):
    __tablename__ = "payday_transfer"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("payday_run.id"))
    line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_line.id"))
    name: Mapped[str] = mapped_column(String(200))
    from_account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    to_account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    suggested_pence: Mapped[int] = mapped_column(Integer)
    actual_pence: Mapped[int | None] = mapped_column(Integer)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    # "bank" when a matching transfer in the bank data ticked it, rather than a person.
    done_via: Mapped[str | None] = mapped_column(String(16))
    matched_transaction_id: Mapped[int | None] = mapped_column(ForeignKey("bank_transaction.id"))

    run: Mapped[PaydayRun] = relationship(back_populates="transfers")
    from_account: Mapped[Account] = relationship(foreign_keys=[from_account_id])
    to_account: Mapped[Account] = relationship(foreign_keys=[to_account_id])


# ---- transactions and the inbox (PLAN.md §8.3, §8.4) ------------------------------------


class ImportBatch(Base):
    """One CSV (later: one bank sync) brought into one account."""

    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    source: Mapped[str] = mapped_column(String(32))  # "csv" now; bank providers in Phase 4
    format: Mapped[str] = mapped_column(String(32))  # monzo | starling | rbs | mapped
    filename: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_new: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[Account] = relationship()


class BankTransaction(Base):
    """A booked (or pending) movement on one of our accounts. Money out is negative."""

    __tablename__ = "bank_transaction"
    __table_args__ = (UniqueConstraint("account_id", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), index=True)
    # The bank's own id where it gives one; otherwise a hash of date, amount, text and position.
    external_id: Mapped[str] = mapped_column(String(128))
    booked_date: Mapped[date] = mapped_column(Date, index=True)
    value_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        _enum("pending", "booked", name="transaction_status"), default="booked"
    )
    amount_pence: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    counterparty: Mapped[str | None] = mapped_column(String(255))
    merchant_normalised: Mapped[str] = mapped_column(String(255), index=True)
    bank_category: Mapped[str | None] = mapped_column(String(100))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    matched_line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_line.id"), index=True)
    # auto | rule | manual | transfer: how the match was made, so re-matching respects people.
    match_method: Mapped[str | None] = mapped_column(String(16))
    # The other side of a transfer between our own accounts.
    transfer_pair_id: Mapped[int | None] = mapped_column(ForeignKey("bank_transaction.id"))
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batch.id"))
    # Set when the row came from a bank connection rather than a CSV.
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("bank_connection.id"), index=True)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[Account] = relationship()
    category: Mapped[Category | None] = relationship()
    matched_line: Mapped[BudgetLine | None] = relationship()


class MerchantAlias(Base):
    """User-editable: bank text containing `pattern` is the merchant `merchant`."""

    __tablename__ = "merchant_alias"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(String(200), unique=True)
    merchant: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CategoryRule(Base):
    """ "Always match this payee": applied before automatic matching, highest priority first."""

    __tablename__ = "category_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    # e.g. {"merchant": "Tesco", "account": "joint_billing"}; every key given must match.
    match_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    set_category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id"))
    set_line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_line.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InboxItem(Base):
    """Something bank data proposes; nothing changes the budget until a person approves it."""

    __tablename__ = "inbox_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(
        _enum(
            "price_change",
            "new_recurring",
            "missed_payment",
            "consent_expiring",
            "remortgage_window",
            "still_paying",
            "goal_adjust",
            "renewal_due",
            "agreement_ending",
            "salary_mismatch",
            name="inbox_type",
        )
    )
    status: Mapped[str] = mapped_column(
        _enum("open", "approved", "rejected", "snoozed", name="inbox_status"), default="open"
    )
    # Stops the same proposal being raised twice, e.g. "price_change:line:12:10850".
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_line.id"))
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("bank_transaction.id"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    line: Mapped[BudgetLine | None] = relationship()
    transaction: Mapped[BankTransaction | None] = relationship()


# ---- bank connections (PLAN.md §8.1-8.3) -----------------------------------------------


class BankConnection(Base):
    """One person's link to one bank: an Enable Banking session, a Monzo sign-in or a
    Starling token. "pending" is a Monzo sign-in waiting to be approved in the Monzo app.

    Read-only access only. The session id or token is Fernet-encrypted; only its owner can
    re-link or disconnect it, though both of us can see it.
    """

    __tablename__ = "bank_connection"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    provider: Mapped[str] = mapped_column(
        _enum("enable_banking", "starling_pat", "monzo", "demo", name="bank_provider")
    )
    # The bank as the provider names it, e.g. ("RBS", "GB"); the provider's own name for a token.
    bank_name: Mapped[str] = mapped_column(String(100))
    bank_country: Mapped[str] = mapped_column(String(2), default="GB")
    secret_encrypted: Mapped[str] = mapped_column(Text)
    consent_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        _enum("pending", "active", "expired", "revoked", "error", name="bank_connection_status"),
        default="active",
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User] = relationship()
    accounts: Mapped[list["BankAccountLink"]] = relationship(
        back_populates="connection", cascade="all, delete-orphan", order_by="BankAccountLink.id"
    )


class BankAccountLink(Base):
    """An account the bank offered on a connection, and which of ours it feeds (if any)."""

    __tablename__ = "bank_account_link"
    __table_args__ = (UniqueConstraint("connection_id", "external_uid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("bank_connection.id"))
    external_uid: Mapped[str] = mapped_column(String(128))
    # Stable across re-links (the provider's uid may not be), and spots the joint account
    # turning up on both people's connections.
    identification_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str | None] = mapped_column(String(100))  # e.g. "Joint", "...1234"
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    account_id: Mapped[int | None] = mapped_column(ForeignKey("account.id"))
    # The bank covers this account from here on (the day after its last CSV row, decided at
    # link time); CSV rows from these days are skipped as already synced.
    synced_from: Mapped[date | None] = mapped_column(Date)
    synced_through: Mapped[date | None] = mapped_column(Date)
    balance_pence: Mapped[int | None] = mapped_column(Integer)
    balance_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    connection: Mapped[BankConnection] = relationship(back_populates="accounts")
    account: Mapped[Account | None] = relationship()


class BankLinkFlow(Base):
    """A bank sign-in in progress: the state sent to the bank, used once."""

    __tablename__ = "bank_link_flow"

    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"))
    provider: Mapped[str] = mapped_column(String(32))
    bank_name: Mapped[str] = mapped_column(String(100))
    bank_country: Mapped[str] = mapped_column(String(2))
    relink_connection_id: Mapped[int | None] = mapped_column(ForeignKey("bank_connection.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncRun(Base):
    """One sync of one connection: when, how it was started, and what it brought in."""

    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("bank_connection.id"), index=True)
    trigger: Mapped[str] = mapped_column(String(16))  # schedule | manual | link
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | error
    rows_new: Mapped[int] = mapped_column(Integer, default=0)
    rows_updated: Mapped[int] = mapped_column(Integer, default=0)
    rows_removed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)


# ---- recurring payments and alerts (PLAN.md §8.4, Phase 5) -------------------------------


class RecurringCandidate(Base):
    """A payee paid on a steady rhythm from one account: a bill we know, or one to propose."""

    __tablename__ = "recurring_candidate"
    __table_args__ = (UniqueConstraint("account_id", "merchant_normalised"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    merchant_normalised: Mapped[str] = mapped_column(String(255))
    cadence: Mapped[str] = mapped_column(
        _enum("weekly", "monthly", "quarterly", "annual", name="recurring_cadence")
    )
    typical_amount_pence: Mapped[int] = mapped_column(Integer)  # the latest price, positive
    # A price change seen in the history: what it was before, and when it moved.
    previous_amount_pence: Mapped[int | None] = mapped_column(Integer)
    price_changed_on: Mapped[date | None] = mapped_column(Date)
    day_of_month: Mapped[int | None] = mapped_column(Integer)
    occurrences: Mapped[int] = mapped_column(Integer)
    first_seen: Mapped[date] = mapped_column(Date)
    last_seen: Mapped[date] = mapped_column(Date)
    confidence: Mapped[float] = mapped_column()
    # The live line its payments are matched to, when it's a bill we already budget for.
    line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_line.id"))
    status: Mapped[str] = mapped_column(
        _enum("new", "known", "accepted", "ignored", name="recurring_status"), default="new"
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[Account] = relationship()


class Notification(Base):
    """A Home Assistant notification sent (or tried), so nothing is sent twice."""

    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(primary_key=True)
    # e.g. "inbox:42", "low_balance:2026-10", "promotion_due:3:2027-04-01"
    key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ok: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None] = mapped_column(Text)


class HaToken(Base):
    """A read-only token for Home Assistant's REST sensor. Only its hash is kept."""

    __tablename__ = "ha_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---- payslips (Phase 6: the take-home check) ----------------------------------------------


class Payslip(Base):
    """A payslip's net pay, typed in, to check the take-home calculation against."""

    __tablename__ = "payslip"

    id: Mapped[int] = mapped_column(primary_key=True)
    income_source_id: Mapped[int] = mapped_column(ForeignKey("income_source.id"), index=True)
    on: Mapped[date] = mapped_column(Date)
    net_monthly_pence: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---- forecast paths, one-offs and savings goals (PLAN.md §5.5, Phase 6) --------------------


class ForecastPath(Timestamped, Base):
    """A dated chain of budget versions: what applies from which month."""

    __tablename__ = "forecast_path"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    is_default: Mapped[bool] = mapped_column(default=False)
    notes: Mapped[str | None] = mapped_column(Text)

    steps: Mapped[list["ForecastStep"]] = relationship(
        back_populates="path", cascade="all, delete-orphan", order_by="ForecastStep.from_month"
    )


class ForecastStep(Base):
    __tablename__ = "forecast_step"

    id: Mapped[int] = mapped_column(primary_key=True)
    path_id: Mapped[int] = mapped_column(ForeignKey("forecast_path.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("budget_version.id"))
    from_month: Mapped[date] = mapped_column(Date)  # the 1st; the first step's is the start

    path: Mapped[ForecastPath] = relationship(back_populates="steps")
    version: Mapped[BudgetVersion] = relationship()


class OneOff(Timestamped, Base):
    """A dated amount in or out of an account, on one path or on every path."""

    __tablename__ = "one_off"

    id: Mapped[int] = mapped_column(primary_key=True)
    path_id: Mapped[int | None] = mapped_column(ForeignKey("forecast_path.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    on: Mapped[date] = mapped_column(Date)
    amount_pence: Mapped[int] = mapped_column(Integer)  # in positive, out negative
    label: Mapped[str] = mapped_column(String(200))

    account: Mapped[Account] = relationship()


class SavingsGoal(Timestamped, Base):
    """Money put aside for a date. A sinking fund (Xmas, Birthdays) repeats every year."""

    __tablename__ = "savings_goal"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    # The allocation line that pays into it; its amount is what the goal proposes changing.
    line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_line.id"))
    target_pence: Mapped[int] = mapped_column(Integer)
    target_date: Mapped[date] = mapped_column(Date)
    saved_pence: Mapped[int] = mapped_column(Integer, default=0)
    saved_on: Mapped[date] = mapped_column(Date)
    repeats_yearly: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(
        _enum("active", "done", "archived", name="goal_status"), default="active"
    )

    account: Mapped[Account] = relationship()
    line: Mapped[BudgetLine | None] = relationship()


# ---- childcare (PLAN.md §9, Phase 6) ------------------------------------------------------


class ChildcareChild(Base):
    __tablename__ = "childcare_child"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    born_on: Mapped[date | None] = mapped_column(Date)
    # Some 2-year-olds qualify for funded hours (e.g. on certain benefits); set by hand.
    eligible_at_two: Mapped[bool] = mapped_column(default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)


class ChildcareNursery(Timestamped, Base):
    __tablename__ = "childcare_nursery"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    basis: Mapped[str] = mapped_column(_enum("day", "hour", name="nursery_basis"))
    takes_tfc: Mapped[bool] = mapped_column(default=True)
    bills_leave: Mapped[bool] = mapped_column(default=False)
    # One per child, in order: [{"rate": "60", "discount_pct": "10", "min_days": "4"}].
    rates_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    discounts_confirmed: Mapped[bool] = mapped_column(default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)


class ChildcarePattern(Base):
    __tablename__ = "childcare_pattern"

    id: Mapped[int] = mapped_column(primary_key=True)
    days: Mapped[str] = mapped_column(String(16))  # exact decimals as text
    hours: Mapped[str] = mapped_column(String(16))
    position: Mapped[int] = mapped_column(Integer, default=0)


class ChildcareProfile(Base):
    """The earner and household inputs (the old calculator's form), one row."""

    __tablename__ = "childcare_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("person.id"))
    # salary, ref_hours, ref_days, bank_holidays, leave_days, partner_income, tax_code,
    # net_pay_arrangement, use_child_benefit, cb_first_weekly, cb_other_weekly, tfc_cap,
    # tfc_rate, ni_primary_threshold, ni_upper_limit, payslip_monthly: decimals as text.
    fields_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ChildcareScenario(Timestamped, Base):
    """A nursery and work pattern, which can be linked to a what-if (PLAN.md §9)."""

    __tablename__ = "childcare_scenario"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    nursery_id: Mapped[int] = mapped_column(ForeignKey("childcare_nursery.id"))
    pattern_id: Mapped[int] = mapped_column(ForeignKey("childcare_pattern.id"))
    version_id: Mapped[int | None] = mapped_column(ForeignKey("budget_version.id"), unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))  # pays the nursery

    nursery: Mapped[ChildcareNursery] = relationship()
    pattern: Mapped[ChildcarePattern] = relationship()
    version: Mapped[BudgetVersion | None] = relationship()
    account: Mapped[Account] = relationship()


# ---- the mortgage (PLAN.md §10, Phase 7) ---------------------------------------------------


class Mortgage(Timestamped, Base):
    __tablename__ = "mortgage"

    id: Mapped[int] = mapped_column(primary_key=True)
    lender: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(_enum("current", "past", name="mortgage_status"))
    principal_pence: Mapped[int | None] = mapped_column(Integer)
    rate_pct: Mapped[str | None] = mapped_column(String(16))  # exact decimal as text
    term_months: Mapped[int | None] = mapped_column(Integer)
    started_on: Mapped[date | None] = mapped_column(Date)
    first_payment_on: Mapped[date | None] = mapped_column(Date)
    ended_on: Mapped[date | None] = mapped_column(Date)
    fix_end_on: Mapped[date | None] = mapped_column(Date)
    fix_end_exact: Mapped[bool] = mapped_column(default=False)
    payment_pence: Mapped[int | None] = mapped_column(Integer)
    overpay_allowance_pct: Mapped[str] = mapped_column(String(16), default="10")
    allowance_basis: Mapped[str] = mapped_column(
        _enum("anniversary", "calendar_year", name="allowance_basis"), default="anniversary"
    )
    property_value_pence: Mapped[int | None] = mapped_column(Integer)
    property_value_on: Mapped[date | None] = mapped_column(Date)
    # The budget line whose amount is this mortgage's payment (one source of truth).
    line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_line.id"))
    notes: Mapped[str | None] = mapped_column(Text)

    statements: Mapped[list["MortgageStatement"]] = relationship(
        back_populates="mortgage", cascade="all, delete-orphan", order_by="MortgageStatement.on"
    )


class MortgageStatement(Base):
    __tablename__ = "mortgage_statement"

    id: Mapped[int] = mapped_column(primary_key=True)
    mortgage_id: Mapped[int] = mapped_column(ForeignKey("mortgage.id"), index=True)
    on: Mapped[date] = mapped_column(Date)
    balance_pence: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    mortgage: Mapped[Mortgage] = relationship(back_populates="statements")


# ---- renewals (Phase 7) ------------------------------------------------------------------


class Renewal(Timestamped, Base):
    """A policy or contract that renews on a date, linked to the bill line that pays it."""

    __tablename__ = "renewal"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str | None] = mapped_column(String(200))
    line_id: Mapped[int | None] = mapped_column(ForeignKey("budget_line.id"))
    renews_on: Mapped[date] = mapped_column(Date)
    cost_pence: Mapped[int | None] = mapped_column(Integer)  # the last year's price
    remind_days: Mapped[int] = mapped_column(Integer, default=30)
    notes: Mapped[str | None] = mapped_column(Text)

    line: Mapped[BudgetLine | None] = relationship()
