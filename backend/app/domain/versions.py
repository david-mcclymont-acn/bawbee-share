"""Version resolution (PLAN.md §5.2): a what-if is its base plus its own overrides.

    resolve(live)    = live lines, at their line version effective on `as_of`
    resolve(what_if) = apply(what_if.overrides, resolve(what_if.base))
    resolve(period)  = the frozen snapshot

Settings and incomes resolve the same way: the most specific version that sets a value wins.
"""

from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date
from fractions import Fraction
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import must_get
from app.domain.models import (
    Account,
    BudgetLine,
    BudgetLineVersion,
    BudgetVersion,
    Category,
    IncomeSource,
    IncomeVersion,
    Override,
    Person,
)
from app.engine import budget as engine
from app.engine.money import monthly_equivalent

DEFAULT_SETTINGS: dict[str, Any] = {
    "split_basis": "take_home",
    "fixed_shares": {},
    "rounding_step_pence": 500,
    "buffer_pence": 500,
    "extra_lines_mode": "on_top_of_share",
}

# Fields stored on BudgetLine (identity) vs BudgetLineVersion (dated values).
LINE_LEVEL_FIELDS = (
    "name",
    "provider",
    "kind",
    "account",
    "to_account",
    "category",
    "owner",
    "agreement",
    "tags",
    "flags",
    "notes",
)
VERSION_LEVEL_FIELDS = (
    "amount_pence",
    "frequency",
    "day_of_month",
    "renewal_date",
    "transfer_rule",
    "actual_override_pence",
    "components",
)
EDITABLE_FIELDS = LINE_LEVEL_FIELDS + VERSION_LEVEL_FIELDS

# "computed": made by a module (a linked childcare scenario), not entered; never editable.
Origin = Literal["base", "modified", "added", "computed"]


class VersionError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedLine:
    line_id: int
    name: str
    kind: str
    account: str
    provider: str | None = None
    to_account: str | None = None
    category: str | None = None
    owner: str | None = None
    amount_pence: int | None = None
    frequency: str = "monthly"
    day_of_month: int | None = None
    renewal_date: str | None = None
    transfer_rule: dict[str, Any] | None = None
    actual_override_pence: int | None = None
    components: list[dict[str, Any]] = field(default_factory=list)
    agreement: dict[str, Any] | None = None
    tags: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    notes: str | None = None
    # Provenance, relative to the version being resolved.
    line_version_id: int | None = None
    origin: Origin = "base"
    override_id: int | None = None

    @property
    def key(self) -> str:
        return line_key(self.line_id)

    @property
    def label(self) -> str:
        return f"{self.name} ({self.provider})" if self.provider else self.name

    def values(self) -> dict[str, Any]:
        return {f: getattr(self, f) for f in EDITABLE_FIELDS}

    def monthly(self) -> Fraction:
        return monthly_equivalent(self.amount_pence or 0, self.frequency)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ResolvedIncome:
    person: str
    net_monthly_pence: int | None
    gross_annual_pence: int
    benefits_allowance_pence: int
    as_of_date: date
    employer: str | None


@dataclass(frozen=True)
class AccountInfo:
    key: str
    name: str
    role: str
    owner: str | None


@dataclass
class ResolvedBudget:
    version_id: int
    version_name: str
    version_type: str
    as_of: date
    lines: list[ResolvedLine]
    settings: dict[str, Any]
    incomes: list[ResolvedIncome]
    accounts: list[AccountInfo]

    def line(self, line_id: int) -> ResolvedLine:
        return next(ln for ln in self.lines if ln.line_id == line_id)

    def find(self, name: str, account: str | None = None) -> ResolvedLine:
        matches = [
            ln
            for ln in self.lines
            if ln.name == name and (account is None or ln.account == account)
        ]
        if len(matches) != 1:
            raise LookupError(f"{len(matches)} lines named {name!r} in {account or 'any account'}")
        return matches[0]

    def by_id(self) -> dict[int, ResolvedLine]:
        return {ln.line_id: ln for ln in self.lines}


def line_key(line_id: int) -> str:
    return f"line:{line_id}"


# ---- lookups ---------------------------------------------------------------------------


@dataclass
class Lookups:
    """Id <-> key maps, so resolved lines can speak in stable keys and names."""

    account_key: dict[int, str]
    account_id: dict[str, int]
    category_name: dict[int, str]
    category_id: dict[str, int]
    person_key: dict[int, str]
    person_id: dict[str, int]

    @classmethod
    def load(cls, session: Session) -> "Lookups":
        accounts = session.scalars(select(Account)).all()
        categories = session.scalars(select(Category)).all()
        people = session.scalars(select(Person)).all()
        return cls(
            account_key={a.id: a.key for a in accounts},
            account_id={a.key: a.id for a in accounts},
            category_name={c.id: c.name for c in categories},
            category_id={c.name: c.id for c in categories},
            person_key={p.id: p.key for p in people},
            person_id={p.key: p.id for p in people},
        )


def line_values(line: BudgetLine, lv: BudgetLineVersion | None, lookups: Lookups) -> dict[str, Any]:
    values: dict[str, Any] = {
        "name": line.name,
        "provider": line.provider,
        "kind": line.kind,
        "account": lookups.account_key[line.account_id],
        "to_account": lookups.account_key.get(line.to_account_id) if line.to_account_id else None,
        "category": lookups.category_name.get(line.category_id) if line.category_id else None,
        "owner": lookups.person_key.get(line.owner_person_id) if line.owner_person_id else None,
        "agreement": line.agreement_json,
        "tags": list(line.tags or []),
        "flags": list(line.flags or []),
        "notes": line.notes,
    }
    if lv is not None:
        values.update(
            amount_pence=lv.amount_pence,
            frequency=lv.frequency,
            day_of_month=lv.day_of_month,
            renewal_date=lv.renewal_date.isoformat() if lv.renewal_date else None,
            transfer_rule=lv.transfer_rule_json,
            actual_override_pence=lv.actual_override_pence,
            components=list(lv.components_json or []),
        )
    return values


def check_fields(values: dict[str, Any]) -> None:
    unknown = set(values) - set(EDITABLE_FIELDS)
    if unknown:
        raise VersionError(f"Unknown line fields: {', '.join(sorted(unknown))}")


# ---- queries ---------------------------------------------------------------------------


def live_version(session: Session) -> BudgetVersion:
    live = session.scalars(select(BudgetVersion).where(BudgetVersion.type == "live")).all()
    if len(live) != 1:
        raise VersionError(f"Expected exactly one live budget, found {len(live)}")
    return live[0]


def chain(session: Session, version: BudgetVersion) -> list[BudgetVersion]:
    """Versions from the live budget down to `version`, inclusive."""
    out: list[BudgetVersion] = []
    current: BudgetVersion | None = version
    seen: set[int] = set()
    while current is not None:
        if current.id in seen:
            raise VersionError(f"Version {version.name!r} has a circular base chain")
        seen.add(current.id)
        out.append(current)
        current = (
            session.get(BudgetVersion, current.base_version_id) if current.base_version_id else None
        )
    out.reverse()
    if out[0].type != "live":
        raise VersionError(f"Version {version.name!r} doesn't descend from the live budget")
    return out


def current_line_version(session: Session, line_id: int, as_of: date) -> BudgetLineVersion | None:
    return session.scalars(
        select(BudgetLineVersion)
        .where(
            BudgetLineVersion.line_id == line_id,
            BudgetLineVersion.effective_from <= as_of,
            or_(BudgetLineVersion.effective_to.is_(None), BudgetLineVersion.effective_to > as_of),
        )
        .order_by(BudgetLineVersion.effective_from.desc(), BudgetLineVersion.id.desc())
    ).first()


def _live_lines(session: Session, as_of: date, lookups: Lookups) -> list[ResolvedLine]:
    rows = session.execute(
        select(BudgetLine, BudgetLineVersion)
        .join(BudgetLineVersion, BudgetLineVersion.line_id == BudgetLine.id)
        .where(
            BudgetLine.status == "active",
            BudgetLine.scope_version_id.is_(None),
            BudgetLineVersion.effective_from <= as_of,
            or_(BudgetLineVersion.effective_to.is_(None), BudgetLineVersion.effective_to > as_of),
        )
        .order_by(BudgetLine.id, BudgetLineVersion.effective_from, BudgetLineVersion.id)
    ).all()
    latest: dict[int, tuple[BudgetLine, BudgetLineVersion]] = {}
    for line, lv in rows:
        latest[line.id] = (line, lv)  # later rows win
    return [
        ResolvedLine(line_id=line.id, line_version_id=lv.id, **line_values(line, lv, lookups))
        for line, lv in latest.values()
    ]


def _apply_overrides(
    lines: list[ResolvedLine], overrides: list[Override], mark: bool
) -> list[ResolvedLine]:
    by_id = {ln.line_id: ln for ln in lines}
    order = [ln.line_id for ln in lines]
    for ov in sorted(overrides, key=lambda o: o.id):
        if ov.action == "remove":
            by_id.pop(ov.line_id, None)
        elif ov.action == "modify":
            base = by_id.get(ov.line_id)
            if base is not None:  # an override of a line the base no longer has is orphaned
                by_id[ov.line_id] = replace(
                    base,
                    **ov.fields_json,
                    origin="modified" if mark else "base",
                    override_id=ov.id if mark else None,
                )
        else:
            by_id[ov.line_id] = ResolvedLine(
                line_id=ov.line_id,
                origin="added" if mark else "base",
                override_id=ov.id if mark else None,
                **ov.fields_json,
            )
            order.append(ov.line_id)
    return [by_id[i] for i in dict.fromkeys(order) if i in by_id]


def _incomes(
    session: Session, versions: list[BudgetVersion], lookups: Lookups
) -> list[ResolvedIncome]:
    out: list[ResolvedIncome] = []
    version_rank = {v.id: i for i, v in enumerate(versions)}
    for person_id, person_key in lookups.person_key.items():
        source = session.scalars(
            select(IncomeSource)
            .where(IncomeSource.person_id == person_id)
            .order_by(IncomeSource.as_of_date.desc(), IncomeSource.id.desc())
        ).first()
        if source is None:
            continue
        rows = session.scalars(
            select(IncomeVersion).where(IncomeVersion.income_source_id == source.id)
        ).all()
        # Global rows (no version) first, then down the chain: the most specific wins.
        rows = sorted(
            (r for r in rows if r.version_id is None or r.version_id in version_rank),
            key=lambda r: -1 if r.version_id is None else version_rank[r.version_id],
        )
        net: int | None = None
        gross = source.gross_annual_pence
        for r in rows:
            if r.net_monthly_override_pence is not None:
                net = r.net_monthly_override_pence
            if r.gross_override_pence is not None:
                gross = r.gross_override_pence
        out.append(
            ResolvedIncome(
                person=person_key,
                net_monthly_pence=net,
                gross_annual_pence=gross,
                benefits_allowance_pence=source.benefits_allowance_pence or 0,
                as_of_date=source.as_of_date,
                employer=source.employer,
            )
        )
    return out


def _accounts(session: Session, lookups: Lookups) -> list[AccountInfo]:
    return [
        AccountInfo(
            key=a.key,
            name=a.name,
            role=a.role,
            owner=lookups.person_key.get(a.owner_person_id) if a.owner_person_id else None,
        )
        for a in session.scalars(select(Account).order_by(Account.id))
    ]


def resolve(session: Session, version: BudgetVersion, as_of: date) -> ResolvedBudget:
    if version.type == "period":
        if version.snapshot_json is None:
            raise VersionError(f"Period {version.name!r} has no snapshot")
        return from_snapshot(version, version.snapshot_json)

    lookups = Lookups.load(session)
    versions = chain(session, version)
    lines = _live_lines(session, as_of, lookups)
    settings = dict(DEFAULT_SETTINGS)
    for v in versions:
        settings.update(v.settings_json or {})
        if v.type == "what_if":
            lines = _apply_overrides(lines, list(v.overrides), mark=v.id == version.id)
    resolved = ResolvedBudget(
        version_id=version.id,
        version_name=version.name,
        version_type=version.type,
        as_of=as_of,
        lines=lines,
        settings=settings,
        incomes=_incomes(session, versions, lookups),
        accounts=_accounts(session, lookups),
    )
    if version.type == "what_if":
        from app.domain import childcare  # a linked childcare scenario (PLAN.md §9)

        childcare.apply_link(session, versions, resolved)
    return resolved


# ---- snapshots (frozen periods) ----------------------------------------------------------


def to_snapshot(resolved: ResolvedBudget) -> dict[str, Any]:
    return {
        "as_of": resolved.as_of.isoformat(),
        "lines": [{**asdict(ln), "origin": "base", "override_id": None} for ln in resolved.lines],
        "settings": resolved.settings,
        "incomes": [
            {**asdict(i), "as_of_date": i.as_of_date.isoformat()} for i in resolved.incomes
        ],
        "accounts": [asdict(a) for a in resolved.accounts],
    }


def from_snapshot(version: BudgetVersion, snap: dict[str, Any]) -> ResolvedBudget:
    line_fields = {f.name for f in fields(ResolvedLine)}
    return ResolvedBudget(
        version_id=version.id,
        version_name=version.name,
        version_type=version.type,
        as_of=date.fromisoformat(snap["as_of"]),
        lines=[
            ResolvedLine(**{k: v for k, v in ln.items() if k in line_fields})
            for ln in snap["lines"]
        ],
        settings=snap["settings"],
        incomes=[
            ResolvedIncome(**{**i, "as_of_date": date.fromisoformat(i["as_of_date"])})
            for i in snap["incomes"]
        ],
        accounts=[AccountInfo(**a) for a in snap["accounts"]],
    )


# ---- comparisons ------------------------------------------------------------------------


@dataclass(frozen=True)
class LineChange:
    line_id: int
    label: str
    account: str
    change: Literal["added", "removed", "modified"]
    # field -> (before, after); for added/removed every field is listed.
    fields: dict[str, tuple[Any, Any]]


def diff(before: ResolvedBudget, after: ResolvedBudget) -> list[LineChange]:
    a, b = before.by_id(), after.by_id()
    changes: list[LineChange] = []
    for line_id, old in a.items():
        new = b.get(line_id)
        if new is None:
            changes.append(
                LineChange(
                    line_id,
                    old.label,
                    old.account,
                    "removed",
                    {k: (v, None) for k, v in old.values().items()},
                )
            )
            continue
        changed = {
            k: (old.values()[k], new.values()[k])
            for k in EDITABLE_FIELDS
            if old.values()[k] != new.values()[k]
        }
        if changed:
            changes.append(LineChange(line_id, new.label, new.account, "modified", changed))
    for line_id, new in b.items():
        if line_id not in a:
            changes.append(
                LineChange(
                    line_id,
                    new.label,
                    new.account,
                    "added",
                    {k: (None, v) for k, v in new.values().items()},
                )
            )
    return changes


@dataclass(frozen=True)
class Conflict:
    """The live line changed after this override was made (PLAN.md §5.4)."""

    override_id: int
    line_id: int
    label: str
    action: str
    # Live values when the override was made, live values now, and the override's values.
    based_on: dict[str, Any]
    live_now: dict[str, Any]
    override: dict[str, Any]


def conflicts(session: Session, version: BudgetVersion, as_of: date) -> list[Conflict]:
    lookups = Lookups.load(session)
    out: list[Conflict] = []
    for ov in sorted(version.overrides, key=lambda o: o.id):
        if ov.action == "add" or ov.based_on_version_id is None:
            continue
        current = current_line_version(session, ov.line_id, as_of)
        if current is not None and current.id == ov.based_on_version_id:
            continue
        old = must_get(session, BudgetLineVersion, ov.based_on_version_id)
        line = must_get(session, BudgetLine, ov.line_id)
        out.append(
            Conflict(
                override_id=ov.id,
                line_id=ov.line_id,
                label=f"{line.name} ({line.provider})" if line.provider else line.name,
                action=ov.action,
                based_on=line_values(line, old, lookups),
                live_now=line_values(line, current, lookups) if current else {},
                override=dict(ov.fields_json),
            )
        )
    return out


def redundant_overrides(session: Session, version: BudgetVersion, as_of: date) -> list[Override]:
    """Modify overrides that now match their base exactly (PLAN.md §5.3)."""
    if version.base is None:
        return []
    base = resolve(session, version.base, as_of).by_id()
    out = []
    for ov in version.overrides:
        if ov.action != "modify" or ov.line_id not in base:
            continue
        base_values = base[ov.line_id].values()
        if all(base_values.get(k) == v for k, v in ov.fields_json.items()):
            out.append(ov)
    return out


# ---- engine adapter ---------------------------------------------------------------------


def to_engine(resolved: ResolvedBudget) -> engine.Budget:
    def rule(raw: dict[str, Any] | None) -> engine.TransferRule | None:
        if raw is None:
            return None
        if raw["type"] == "joint_share":
            return engine.JointShareRule(
                person=raw["person"],
                extra_line_keys=tuple(line_key(i) for i in raw.get("extra_line_ids", [])),
            )
        if raw["type"] == "cover_account_lines":
            return engine.CoverAccountRule(
                account=raw["account"],
                rounding_step_pence=raw.get("rounding_step_pence", 500),
                buffer_pence=raw.get("buffer_pence", 0),
            )
        raise VersionError(f"Unknown transfer rule {raw['type']!r}")

    s = resolved.settings
    return engine.Budget(
        accounts=tuple(
            engine.Account(key=a.key, role=a.role, owner=a.owner)  # type: ignore[arg-type]
            for a in resolved.accounts
        ),
        lines=tuple(
            engine.Line(
                key=ln.key,
                name=ln.name,
                kind=ln.kind,  # type: ignore[arg-type]
                account=ln.account,
                amount_pence=ln.amount_pence,
                frequency=ln.frequency,  # type: ignore[arg-type]
                to_account=ln.to_account,
                rule=rule(ln.transfer_rule),
                actual_pence=ln.actual_override_pence,
                day_of_month=ln.day_of_month,
                provider=ln.provider,
            )
            for ln in resolved.lines
        ),
        incomes=tuple(
            engine.Income(
                person=i.person,
                net_monthly_pence=i.net_monthly_pence or 0,
                gross_annual_pence=i.gross_annual_pence,
                benefits_allowance_pence=i.benefits_allowance_pence,
            )
            for i in resolved.incomes
        ),
        settings=engine.Settings(
            split_basis=s["split_basis"],
            fixed_shares={p: Fraction(str(v)) for p, v in (s.get("fixed_shares") or {}).items()},
            rounding_step_pence=s["rounding_step_pence"],
            buffer_pence=s["buffer_pence"],
            extra_lines_mode=s["extra_lines_mode"],
        ),
    )


def calculate(resolved: ResolvedBudget) -> engine.Result:
    return engine.calculate(to_engine(resolved))
