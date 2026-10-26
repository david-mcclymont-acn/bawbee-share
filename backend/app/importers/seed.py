"""Import seed/budget-seed.json (PLAN.md §14 Phase 1).

- Maternity becomes the live budget (decision 22).
- Back to normal becomes the what-if "Back to normal", holding only its differences
  from Maternity (decision 1).
- Follow-up flags become FollowUp items (decision 17).
- post_import_cleanups run afterwards, unless asked not to (the golden tests check the
  spreadsheet's own numbers first).
"""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import must_get
from app.domain import audit, editing
from app.domain.models import (
    Account,
    BudgetLine,
    BudgetVersion,
    Category,
    FollowUp,
    IncomeSource,
    IncomeVersion,
    Override,
    PayDayRule,
    Person,
    User,
)
from app.domain.versions import current_line_version, live_version, resolve

BACK_TO_NORMAL_NAME = "Back to normal"
SPLIT_BASIS = {
    "gross_salary_incl_benefits": "gross",
    "gross_salary": "gross",
    "take_home": "take_home",
}
# Bills whose price changed between the two sheets (PLAN.md §4.6). After import, Back to normal
# follows live's current price for these (decision 28).
CURRENT_PRICE_LINES = {
    ("Pet Insurance", "Ultimate Pet Partners"),
    ("TV", "Sky"),
    ("Council Tax", "Exampletown"),
    ("Spotify", "PayPal"),
    ("Car Insurance", "1stCentral"),
    ("Broadband", "Vodafone"),
}
PRICE_FIELDS = ("amount_pence", "components")
MORTGAGE_MISMATCH_NOTE = (
    "The budget says £1,192.46 a month but the Mortgage Info sheet says £1,193.80. "
    "Check the current payment with Northfield."
)


class SeedError(ValueError):
    pass


# The spreadsheet's shorthand, as the people reading the follow-ups would say it.
_NOTE_REWRITES = {
    "Pink highlight in Maternity sheet: follow up (cancel / check price / check which account)": (
        "Highlighted in the old spreadsheet. Decide whether to cancel it, check its price, "
        "or move it to the right account."
    ),
    "Marked CANCEL!! in sheet": "Marked to cancel in the old spreadsheet.",
}


def friendly_note(note: str) -> str:
    return _NOTE_REWRITES.get(note.strip(), note)


@dataclass
class ImportResult:
    live: BudgetVersion
    what_if: BudgetVersion
    lines: int
    overrides: int
    follow_ups: int
    cleanups: list[str] = field(default_factory=list)


def load_seed(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data


def _scenario(seed: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    return next(s for s in seed["scenarios"] if s["id"] == scenario_id)


def _joint_settings(scenario: dict[str, Any]) -> dict[str, Any]:
    rule = next(
        ln["transfer_rule"]
        for ln in scenario["lines"]
        if (ln.get("transfer_rule") or {}).get("type") == "joint_share"
    )
    return {
        "split_basis": SPLIT_BASIS[rule["split_basis"]],
        "rounding_step_pence": rule["round_up_to_pence"],
        "buffer_pence": rule["buffer_pence"],
        "extra_lines_mode": rule.get("extra_lines_mode", "on_top_of_share"),
    }


def _values(
    raw: dict[str, Any],
    owners: dict[str, str | None],
    joint_line_ids: dict[str, int],
) -> dict[str, Any]:
    """Seed line -> resolved-line field values."""
    rule = raw.get("transfer_rule")
    transfer_rule: dict[str, Any] | None = None
    if rule and rule["type"] == "joint_share":
        transfer_rule = {
            "type": "joint_share",
            "person": owners[raw["account"]],
            "extra_line_ids": [joint_line_ids[n] for n in rule.get("extra_lines_paid_100pct", [])],
        }
    elif rule and rule["type"] == "cover_account_lines":
        transfer_rule = {
            "type": "cover_account_lines",
            "account": rule["account"],
            "rounding_step_pence": rule["round_up_to_pence"],
            "buffer_pence": rule["buffer_pence"],
        }
    agreement = raw.get("agreement")
    if agreement:
        agreement = {
            "type": agreement["type"],
            "end_date": agreement.get("end_date_approx"),
            "end_date_confirmed": agreement.get("end_date_confirmed", False),
            "balloon_payment_pence": agreement.get("balloon_payment_pence"),
            "note": agreement.get("note"),
        }
    return {
        "name": raw["name"],
        "provider": raw.get("provider"),
        "kind": raw["kind"],
        "account": raw["account"],
        "to_account": raw.get("to_account"),
        "category": raw.get("category"),
        "owner": None if raw.get("owner") in (None, "joint") else raw["owner"],
        "agreement": agreement,
        "tags": [],
        "flags": list(raw.get("flags") or []),
        "notes": raw.get("notes"),
        "amount_pence": raw.get("amount_pence"),
        "frequency": raw.get("frequency") or "monthly",
        "day_of_month": raw.get("day_of_month"),
        "renewal_date": None,
        "transfer_rule": transfer_rule,
        "actual_override_pence": raw.get("actual_override_pence"),
        "components": [
            {"label": None, "amount_pence": p} for p in raw.get("components_pence") or []
        ],
    }


def _match(std: list[dict[str, Any]], mat: list[dict[str, Any]]) -> dict[int, int]:
    """Pair each standard line with its Maternity counterpart (by index)."""
    pairs: dict[int, int] = {}
    taken: set[int] = set()

    def candidates(s: dict[str, Any]) -> list[int]:
        same_account = [
            i for i, m in enumerate(mat) if m["account"] == s["account"] and i not in taken
        ]
        if s["kind"] == "transfer":
            return [
                i
                for i in same_account
                if mat[i]["kind"] == "transfer" and mat[i].get("to_account") == s.get("to_account")
            ]
        exact = [
            i
            for i in same_account
            if mat[i]["name"] == s["name"] and mat[i].get("provider") == s.get("provider")
        ]
        return exact or [i for i in same_account if mat[i]["name"] == s["name"]]

    for si, s in enumerate(std):
        found = candidates(s)
        if len(found) == 1:
            pairs[si] = found[0]
            taken.add(found[0])
    return pairs


def import_seed(
    session: Session,
    seed: dict[str, Any],
    as_of: date,
    actor: User | None = None,
    apply_cleanups: bool = True,
) -> ImportResult:
    if session.scalars(select(BudgetVersion)).first() is not None:
        raise SeedError("The database already has budgets; import into an empty database")

    people = {p["id"]: Person(key=p["id"], name=p["name"]) for p in seed["people"]}
    session.add_all(people.values())
    session.flush()

    owners: dict[str, str | None] = {}
    for a in seed["accounts"]:
        owner = None if a["owner"] == "joint" else a["owner"]
        owners[a["id"]] = owner
        session.add(
            Account(
                key=a["id"],
                name=a["name"],
                owner_person_id=people[owner].id if owner else None,
                provider=a.get("provider"),
                role=a["role"],
                notes=a.get("notes"),
            )
        )
    categories = sorted(
        {ln["category"] for s in seed["scenarios"] for ln in s["lines"] if ln.get("category")}
    )
    session.add_all(Category(name=c) for c in categories)
    session.flush()

    live_id = seed.get("scenarios_live_id", "maternity")
    mat = _scenario(seed, live_id)
    std = next(s for s in seed["scenarios"] if s["id"] != live_id)

    # ---- the live budget (Maternity) ----
    live = BudgetVersion(
        name=mat["label"],
        description=f"Imported from the {mat['source_sheet']} sheet.",
        type="live",
        status="active",
        period_start=as_of,
        settings_json=_joint_settings(mat),
    )
    session.add(live)
    session.flush()

    mat_line_ids: list[int] = []
    for raw in mat["lines"]:
        values = _values(raw | {"transfer_rule": None}, owners, {})
        mat_line_ids.append(editing.add_live_line(session, values, as_of, actor).id)
    joint_ids = {
        raw["name"]: line_id
        for raw, line_id in zip(mat["lines"], mat_line_ids, strict=True)
        if raw["account"] == next(a["id"] for a in seed["accounts"] if a["role"] == "joint_bills")
    }
    # Transfer rules can name other lines, so they're filled in once every line exists.
    for raw, line_id in zip(mat["lines"], mat_line_ids, strict=True):
        if raw.get("transfer_rule"):
            lv = current_line_version(session, line_id, as_of)
            if lv is None:
                raise SeedError(f"Line {raw['name']!r} has no version on {as_of}")
            lv.transfer_rule_json = _values(raw, owners, joint_ids)["transfer_rule"]

    reference = seed["reference_income"]
    as_of_income = date.fromisoformat(f"{reference['as_of']}-01")
    sources: dict[str, IncomeSource] = {}
    for key in people:
        sources[key] = IncomeSource(
            person_id=people[key].id,
            employer=mat["income"][key].get("employer"),
            as_of_date=as_of_income,
            gross_annual_pence=reference[f"{key}_gross_salary_pence"],
            benefits_allowance_pence=reference.get(f"{key}_benefits_allowance_pence"),
            bonus_pence=reference.get(f"{key}_bonus_pence"),
        )
    session.add_all(sources.values())
    session.flush()
    for key, source in sources.items():
        session.add(
            IncomeVersion(
                income_source_id=source.id,
                version_id=live.id,
                net_monthly_override_pence=mat["income"][key]["net_monthly_pence"],
            )
        )

    for key, rule in seed.get("pay_days", {}).items():
        session.add(
            PayDayRule(
                person_id=people[key].id,
                rule=rule["rule"],
                typical_offset_days=rule.get("typical_offset_days", 0),
                calendar="scotland",
                notes=rule.get("note"),
            )
        )
    session.flush()

    # ---- the what-if (Back to normal), as a diff against live ----
    what_if = editing.create_what_if(
        session,
        BACK_TO_NORMAL_NAME,
        live,
        actor,
        description=f"Imported from the {std['source_sheet']} sheet: the standard budget "
        "before maternity leave.",
    )
    std_settings = _joint_settings(std)
    editing.set_settings(
        session,
        what_if,
        {k: v for k, v in std_settings.items() if live.settings_json.get(k) != v},
        actor,
    )
    for key, source in sources.items():
        net = std["income"][key]["net_monthly_pence"]
        if net != mat["income"][key]["net_monthly_pence"]:
            editing.set_net_income(session, what_if, source, net, actor)

    pairs = _match(std["lines"], mat["lines"])
    live_lines = resolve(session, live, as_of).by_id()
    std_line_ids: dict[int, int] = {}
    for si, raw in enumerate(std["lines"]):
        values = _values(raw, owners, joint_ids)
        if si in pairs:
            line_id = mat_line_ids[pairs[si]]
            std_line_ids[si] = line_id
            base = live_lines[line_id].values()
            changed = {k: v for k, v in values.items() if base[k] != v}
            # A sheet that just doesn't itemise a bill isn't a difference; only clear the
            # breakdown when the amount itself changed.
            if not values["components"] and "amount_pence" not in changed:
                changed.pop("components", None)
            if changed:
                editing.override_line(session, what_if, line_id, changed, as_of, actor)
        else:
            std_line_ids[si] = editing.add_line(session, what_if, values, actor).line_id
    matched = set(pairs.values())
    for mi, line_id in enumerate(mat_line_ids):
        if mi not in matched:
            editing.remove_line(session, what_if, line_id, as_of, actor)
    session.flush()

    # ---- follow-ups ----
    seen: set[tuple[int, str, str]] = set()
    follow_ups = 0

    def follow_up(line_id: int, reason: str, note: str) -> None:
        nonlocal follow_ups
        if (line_id, reason, note) in seen:
            return
        seen.add((line_id, reason, note))
        session.add(FollowUp(line_id=line_id, reason=reason, note=note, status="open"))
        follow_ups += 1

    for scenario, ids in ((mat, dict(enumerate(mat_line_ids))), (std, std_line_ids)):
        for i, raw in enumerate(scenario["lines"]):
            for item in raw.get("follow_ups") or []:
                follow_up(ids[i], item["reason"], friendly_note(item.get("note") or ""))
    mortgage = next((i for i, raw in enumerate(mat["lines"]) if raw["name"] == "Mortgage"), None)
    if mortgage is not None:
        follow_up(mat_line_ids[mortgage], "check_price", MORTGAGE_MISMATCH_NOTE)
    session.flush()

    audit.record(
        session,
        actor,
        "seed",
        None,
        "import",
        None,
        {
            "live_version_id": live.id,
            "what_if_id": what_if.id,
            "lines": len(mat_line_ids),
            "overrides": len(what_if.overrides),
            "follow_ups": follow_ups,
        },
    )
    result = ImportResult(live, what_if, len(mat_line_ids), len(what_if.overrides), follow_ups)
    if apply_cleanups:
        result.cleanups = apply_post_import_cleanups(session, as_of, actor)
    return result


def apply_post_import_cleanups(
    session: Session, as_of: date, actor: User | None = None
) -> list[str]:
    """Fixes the spreadsheet needs once parity is proven (PLAN.md decisions 16 and 28)."""
    applied = [
        name
        for name, cleanup in (
            ("remove_joint_apple_fitness", _remove_joint_apple_fitness),
            ("back_to_normal_uses_current_prices", _back_to_normal_uses_current_prices),
        )
        if cleanup(session, as_of, actor)
    ]
    session.flush()
    audit.record(session, actor, "seed", None, "cleanup", None, {"applied": applied})
    return applied


def _back_to_normal_uses_current_prices(session: Session, as_of: date, actor: User | None) -> bool:
    """Back to normal follows live for the bills whose price changed (§4.6), rather than keeping
    the prices the old sheet had (decision 28)."""
    what_if = session.scalars(
        select(BudgetVersion).where(
            BudgetVersion.name == BACK_TO_NORMAL_NAME,
            BudgetVersion.type == "what_if",
            BudgetVersion.status == "draft",
        )
    ).first()
    if what_if is None:
        return False
    changed = False
    for ov in list(what_if.overrides):
        if ov.action != "modify" or (ov.line.name, ov.line.provider) not in CURRENT_PRICE_LINES:
            continue
        remaining = {k: v for k, v in ov.fields_json.items() if k not in PRICE_FIELDS}
        if remaining == ov.fields_json:
            continue
        if remaining:
            before = dict(ov.fields_json)
            ov.fields_json = remaining
            audit.record(session, actor, "override", ov.id, "update", before, remaining)
        else:
            editing.revert_override(session, ov, actor)
        changed = True
    return changed


def _remove_joint_apple_fitness(session: Session, as_of: date, actor: User | None) -> bool:
    """Apple Fitness+ belongs only on Person 1 Starling (decision 16, §4.5)."""
    resolved = resolve(session, live_version(session), as_of)
    joint = next(a.key for a in resolved.accounts if a.role == "joint_bills")
    try:
        fitness = resolved.find("Apple Fitness+", joint)
    except LookupError:
        return False

    line = must_get(session, BudgetLine, fitness.line_id)
    editing.end_live_line(session, line, as_of, actor)

    # Joint transfer rules stop paying for it in full.
    rebased: dict[int, int] = {}
    for ln in resolved.lines:
        rule = ln.transfer_rule
        if rule and fitness.line_id in rule.get("extra_line_ids", []):
            new_rule = rule | {
                "extra_line_ids": [i for i in rule["extra_line_ids"] if i != fitness.line_id]
            }
            transfer_line = must_get(session, BudgetLine, ln.line_id)
            created = editing.update_live_line(
                session, transfer_line, {"transfer_rule": new_rule}, as_of, actor
            )
            if created is not None:
                rebased[ln.line_id] = created.id
    # The cleanup isn't a conflict for what-ifs: re-base their overrides on the new version,
    # and strip the line from any rule a what-if overrides.
    for ov in session.scalars(select(Override)):
        if ov.line_id in rebased and "transfer_rule" not in ov.fields_json:
            ov.based_on_version_id = rebased[ov.line_id]
        rule = ov.fields_json.get("transfer_rule")
        if rule and fitness.line_id in rule.get("extra_line_ids", []):
            ov.fields_json = ov.fields_json | {
                "transfer_rule": rule
                | {"extra_line_ids": [i for i in rule["extra_line_ids"] if i != fitness.line_id]}
            }

    for item in session.scalars(select(FollowUp).where(FollowUp.line_id == fitness.line_id)):
        item.status = "done"
        item.note = (item.note or "") + " Resolved: removed from Joint Billing (decision 16)."
    session.flush()
    return True
