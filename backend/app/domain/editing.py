"""Changing budgets: live lines get new dated versions; what-ifs get sparse overrides.

Every function records an audit entry and flushes; the caller owns the transaction.
"""

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import audit
from app.domain.models import (
    BudgetLine,
    BudgetLineVersion,
    BudgetVersion,
    IncomeSource,
    IncomeVersion,
    Override,
    User,
)
from app.domain.versions import (
    LINE_LEVEL_FIELDS,
    VERSION_LEVEL_FIELDS,
    Lookups,
    VersionError,
    check_fields,
    current_line_version,
    line_values,
    resolve,
)


def apply_line_level(line: BudgetLine, values: dict[str, Any], lookups: Lookups) -> None:
    for name, value in values.items():
        if name == "account":
            line.account_id = lookups.account_id[value]
        elif name == "to_account":
            line.to_account_id = lookups.account_id[value] if value else None
        elif name == "category":
            line.category_id = lookups.category_id[value] if value else None
        elif name == "owner":
            line.owner_person_id = lookups.person_id[value] if value else None
        elif name == "agreement":
            line.agreement_json = value
        elif name in ("tags", "flags"):
            setattr(line, name, list(value or []))
        else:
            setattr(line, name, value)


def version_kwargs(values: dict[str, Any]) -> dict[str, Any]:
    renames = {
        "transfer_rule": "transfer_rule_json",
        "components": "components_json",
    }
    out: dict[str, Any] = {}
    for name, value in values.items():
        if name == "renewal_date":
            value = date.fromisoformat(value) if value else None
        if name == "components":
            value = list(value or [])
        out[renames.get(name, name)] = value
    return out


# ---- the live budget ---------------------------------------------------------------------


def add_live_line(
    session: Session, values: dict[str, Any], effective_from: date, actor: User | None
) -> BudgetLine:
    check_fields(values)
    lookups = Lookups.load(session)
    line = BudgetLine(status="active", name=values["name"], kind=values["kind"])
    apply_line_level(line, {k: v for k, v in values.items() if k in LINE_LEVEL_FIELDS}, lookups)
    session.add(line)
    session.flush()
    version_values = {"frequency": "monthly"} | {
        k: v for k, v in values.items() if k in VERSION_LEVEL_FIELDS
    }
    session.add(
        BudgetLineVersion(
            line_id=line.id,
            effective_from=effective_from,
            created_by=actor.id if actor else None,
            **version_kwargs(version_values),
        )
    )
    session.flush()
    audit.record(session, actor, "budget_line", line.id, "create", None, values)
    return line


def update_live_line(
    session: Session,
    line: BudgetLine,
    changes: dict[str, Any],
    effective_from: date,
    actor: User | None,
) -> BudgetLineVersion | None:
    """Change a live line from `effective_from`. Amount-type fields get a new line version
    (so what-ifs can see the live line moved under them); identity fields change in place."""
    check_fields(changes)
    lookups = Lookups.load(session)
    current = current_line_version(session, line.id, effective_from)
    before = line_values(line, current, lookups)
    apply_line_level(line, {k: v for k, v in changes.items() if k in LINE_LEVEL_FIELDS}, lookups)
    new_version: BudgetLineVersion | None = None
    version_changes = {k: v for k, v in changes.items() if k in VERSION_LEVEL_FIELDS}
    if version_changes:
        if current is None:
            raise VersionError(f"{line.name!r} has no version on {effective_from}")
        merged = {k: before[k] for k in VERSION_LEVEL_FIELDS} | version_changes
        new_version = BudgetLineVersion(
            line_id=line.id,
            effective_from=effective_from,
            effective_to=current.effective_to,
            created_by=actor.id if actor else None,
            **version_kwargs(merged),
        )
        current.effective_to = effective_from
        session.add(new_version)
    session.flush()
    after = line_values(line, new_version or current, lookups)
    audit.record(session, actor, "budget_line", line.id, "update", before, after)
    return new_version


def end_live_line(
    session: Session, line: BudgetLine, effective_from: date, actor: User | None
) -> None:
    """Stop a live line from `effective_from` (its history stays for periods and actuals)."""
    current = current_line_version(session, line.id, effective_from)
    if current is None:
        raise VersionError(f"{line.name!r} isn't live on {effective_from}")
    current.effective_to = effective_from
    session.flush()
    audit.record(
        session,
        actor,
        "budget_line",
        line.id,
        "end",
        None,
        {"effective_to": effective_from.isoformat()},
    )


# ---- what-ifs ----------------------------------------------------------------------------


def create_what_if(
    session: Session,
    name: str,
    base: BudgetVersion,
    actor: User | None,
    description: str | None = None,
    planned_start: date | None = None,
) -> BudgetVersion:
    if base.type == "period":
        raise VersionError("A what-if can't be based on a frozen period")
    version = BudgetVersion(
        name=name,
        description=description,
        type="what_if",
        base_version_id=base.id,
        planned_start=planned_start,
        status="draft",
        settings_json={},
    )
    session.add(version)
    session.flush()
    audit.record(
        session,
        actor,
        "budget_version",
        version.id,
        "create",
        None,
        {"name": name, "base_version_id": base.id},
    )
    return version


def _require_what_if(version: BudgetVersion) -> None:
    if version.type != "what_if" or version.status != "draft":
        raise VersionError(f"{version.name!r} isn't an editable what-if")


def _existing(version: BudgetVersion, line_id: int) -> Override | None:
    return next((o for o in version.overrides if o.line_id == line_id), None)


def override_line(
    session: Session,
    version: BudgetVersion,
    line_id: int,
    changes: dict[str, Any],
    as_of: date,
    actor: User | None,
) -> Override:
    """Change a line in a what-if, recording only the fields that differ."""
    _require_what_if(version)
    check_fields(changes)
    existing = _existing(version, line_id)
    if existing is not None and existing.action == "add":
        before = dict(existing.fields_json)
        existing.fields_json = before | changes
        session.flush()
        audit.record(
            session, actor, "override", existing.id, "update", before, existing.fields_json
        )
        return existing
    if existing is not None and existing.action == "remove":
        raise VersionError("That line is removed in this what-if; revert the removal first")

    if version.base is None:
        raise VersionError(f"{version.name!r} has no base")
    base_line = resolve(session, version.base, as_of).by_id().get(line_id)
    if base_line is None:
        raise VersionError("That line isn't in this what-if's base")
    current = current_line_version(session, line_id, as_of)
    merged = dict(existing.fields_json if existing else {}) | changes
    # Keep only real differences; an override equal to its base is no override at all.
    fields_json = {k: v for k, v in merged.items() if base_line.values()[k] != v}
    previous = dict(existing.fields_json) if existing else None
    if existing is None:
        existing = Override(
            version_id=version.id,
            line_id=line_id,
            action="modify",
            fields_json=fields_json,
            based_on_version_id=current.id if current else None,
            created_by=actor.id if actor else None,
        )
        version.overrides.append(existing)
    else:
        existing.fields_json = fields_json
        existing.based_on_version_id = current.id if current else None
    session.flush()
    audit.record(session, actor, "override", existing.id, "modify", previous, fields_json)
    return existing


def remove_line(
    session: Session, version: BudgetVersion, line_id: int, as_of: date, actor: User | None
) -> Override:
    _require_what_if(version)
    existing = _existing(version, line_id)
    if existing is not None and existing.action == "add":
        raise VersionError("That line was added in this what-if; revert the addition instead")
    current = current_line_version(session, line_id, as_of)
    if existing is None:
        existing = Override(
            version_id=version.id,
            line_id=line_id,
            action="remove",
            fields_json={},
            based_on_version_id=current.id if current else None,
            created_by=actor.id if actor else None,
        )
        version.overrides.append(existing)
    else:
        existing.action, existing.fields_json = "remove", {}
        existing.based_on_version_id = current.id if current else None
    session.flush()
    audit.record(session, actor, "override", existing.id, "remove", None, {"line_id": line_id})
    return existing


def add_line(
    session: Session, version: BudgetVersion, values: dict[str, Any], actor: User | None
) -> Override:
    """Add a line that exists only in this what-if (until it's promoted)."""
    _require_what_if(version)
    check_fields(values)
    lookups = Lookups.load(session)
    values = {"frequency": "monthly"} | values
    line = BudgetLine(
        status="active", name=values["name"], kind=values["kind"], scope_version_id=version.id
    )
    apply_line_level(line, {k: v for k, v in values.items() if k in LINE_LEVEL_FIELDS}, lookups)
    session.add(line)
    session.flush()
    full = (
        line_values(line, None, lookups)
        | {
            "amount_pence": None,
            "frequency": "monthly",
            "day_of_month": None,
            "renewal_date": None,
            "transfer_rule": None,
            "actual_override_pence": None,
            "components": [],
        }
        | {k: v for k, v in values.items() if k in VERSION_LEVEL_FIELDS}
    )
    ov = Override(
        version_id=version.id,
        line_id=line.id,
        action="add",
        fields_json=full,
        created_by=actor.id if actor else None,
    )
    version.overrides.append(ov)
    session.flush()
    audit.record(session, actor, "override", ov.id, "add", None, full)
    return ov


def revert_override(session: Session, override: Override, actor: User | None) -> None:
    """Drop an override so the line follows its base again ("revert to base")."""
    version = override.version
    _require_what_if(version)
    before = {
        "action": override.action,
        "line_id": override.line_id,
        "fields": override.fields_json,
    }
    line = override.line
    version.overrides.remove(override)
    session.flush()
    if override.action == "add" and line.scope_version_id == version.id:
        session.delete(line)
    session.flush()
    audit.record(session, actor, "override", override.id, "revert", before, None)


def set_settings(
    session: Session, version: BudgetVersion, changes: dict[str, Any], actor: User | None
) -> None:
    """Set (or, with a None value, clear) a version's own settings."""
    if version.type == "period":
        raise VersionError("Periods are read-only")
    before = dict(version.settings_json or {})
    after = dict(before)
    for k, v in changes.items():
        if v is None:
            after.pop(k, None)
        else:
            after[k] = v
    version.settings_json = after
    session.flush()
    audit.record(session, actor, "budget_version", version.id, "settings", before, after)


def set_net_income(
    session: Session,
    version: BudgetVersion | None,
    source: IncomeSource,
    net_monthly_pence: int | None,
    actor: User | None,
) -> IncomeVersion:
    """Override monthly take-home for a version (None = every version)."""
    version_id = version.id if version else None
    row = session.scalars(
        select(IncomeVersion).where(
            IncomeVersion.income_source_id == source.id,
            IncomeVersion.version_id.is_(None)
            if version_id is None
            else IncomeVersion.version_id == version_id,
        )
    ).first()
    before = row.net_monthly_override_pence if row else None
    if row is None:
        row = IncomeVersion(income_source_id=source.id, version_id=version_id)
        session.add(row)
    row.net_monthly_override_pence = net_monthly_pence
    session.flush()
    audit.record(session, actor, "income_version", row.id, "set_net", before, net_monthly_pence)
    return row


# ---- managing what-ifs ---------------------------------------------------------------------


def update_what_if(
    session: Session, version: BudgetVersion, changes: dict[str, Any], actor: User | None
) -> None:
    """Rename, describe or (re)schedule a what-if."""
    _require_what_if(version)
    allowed = {"name", "description", "planned_start"}
    unknown = set(changes) - allowed
    if unknown:
        raise VersionError(f"Can't change {', '.join(sorted(unknown))} here")
    before = {k: getattr(version, k) for k in changes}
    for k, v in changes.items():
        if k == "name" and not (v or "").strip():
            raise VersionError("A what-if needs a name")
        setattr(version, k, v.strip() if k == "name" else v)
    session.flush()
    audit.record(
        session,
        actor,
        "budget_version",
        version.id,
        "update",
        {k: str(v) if v is not None else None for k, v in before.items()},
        {k: str(v) if v is not None else None for k, v in changes.items()},
    )


def rebase(
    session: Session, version: BudgetVersion, new_base: BudgetVersion, actor: User | None
) -> None:
    """Build this what-if on a different base. Its overrides stay as they are."""
    _require_what_if(version)
    if new_base.type == "period" or new_base.status in ("promoted", "archived"):
        raise VersionError("A what-if can only be based on live or another open what-if")
    probe: BudgetVersion | None = new_base
    while probe is not None:
        if probe.id == version.id:
            raise VersionError("That would make the what-if its own base")
        probe = probe.base
    before = version.base_version_id
    version.base_version_id = new_base.id
    session.flush()
    session.refresh(version, ["base"])
    audit.record(session, actor, "budget_version", version.id, "rebase", before, new_base.id)


def duplicate_what_if(
    session: Session, version: BudgetVersion, name: str, actor: User | None
) -> BudgetVersion:
    """A new what-if with the same base, settings, incomes and overrides."""
    if version.type != "what_if" or version.base is None:
        raise VersionError("Only what-ifs can be duplicated")
    copy = create_what_if(
        session,
        name,
        version.base,
        actor,
        description=version.description,
        planned_start=version.planned_start,
    )
    copy.settings_json = dict(version.settings_json or {})
    for ov in sorted(version.overrides, key=lambda o: o.id):
        line_id = ov.line_id
        if ov.action == "add":
            original = ov.line
            line = BudgetLine(
                name=original.name,
                provider=original.provider,
                kind=original.kind,
                account_id=original.account_id,
                to_account_id=original.to_account_id,
                category_id=original.category_id,
                owner_person_id=original.owner_person_id,
                tags=list(original.tags or []),
                flags=list(original.flags or []),
                notes=original.notes,
                status="active",
                agreement_json=original.agreement_json,
                scope_version_id=copy.id,
            )
            session.add(line)
            session.flush()
            line_id = line.id
        copy.overrides.append(
            Override(
                version_id=copy.id,
                line_id=line_id,
                action=ov.action,
                fields_json=dict(ov.fields_json),
                based_on_version_id=ov.based_on_version_id,
                created_by=actor.id if actor else None,
            )
        )
    for row in session.scalars(select(IncomeVersion).where(IncomeVersion.version_id == version.id)):
        session.add(
            IncomeVersion(
                income_source_id=row.income_source_id,
                version_id=copy.id,
                net_monthly_override_pence=row.net_monthly_override_pence,
                gross_override_pence=row.gross_override_pence,
            )
        )
    session.flush()
    audit.record(
        session,
        actor,
        "budget_version",
        copy.id,
        "duplicate",
        None,
        {"from_version_id": version.id},
    )
    return copy


def archive_what_if(session: Session, version: BudgetVersion, actor: User | None) -> None:
    _require_what_if(version)
    children = session.scalars(
        select(BudgetVersion).where(
            BudgetVersion.base_version_id == version.id, BudgetVersion.status == "draft"
        )
    ).all()
    if children:
        names = ", ".join(c.name for c in children)
        raise VersionError(f"Other what-ifs are built on this one: {names}. Re-base them first")
    version.status = "archived"
    session.flush()
    audit.record(session, actor, "budget_version", version.id, "archive")


def restore_what_if(session: Session, version: BudgetVersion, actor: User | None) -> None:
    """Bring an archived what-if back (the undo for archive_what_if)."""
    if version.type != "what_if" or version.status != "archived":
        raise VersionError(f"{version.name!r} isn't an archived what-if")
    base = version.base
    if base is None or base.type == "period" or base.status in ("promoted", "archived"):
        # Its old base has gone; build on live instead so it still resolves.
        live = session.scalars(select(BudgetVersion).where(BudgetVersion.type == "live")).one()
        version.base_version_id = live.id
    version.status = "draft"
    session.flush()
    audit.record(session, actor, "budget_version", version.id, "restore")
