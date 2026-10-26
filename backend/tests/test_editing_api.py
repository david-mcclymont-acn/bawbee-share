"""Changing budgets over the API (PLAN.md §5, Phase 2 acceptance)."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import AuditLog, BudgetVersion
from app.importers.seed import ImportResult
from tests.conftest import AS_OF

PARAMS = {"as_of": AS_OF.isoformat()}


def _lines(client: TestClient, version_id: int) -> dict[str, dict[str, Any]]:
    out = {}
    for ln in client.get(f"/api/versions/{version_id}/lines").json():
        v = ln["values"]
        out[f"{v['name']} ({v['provider']})" if v["provider"] else v["name"]] = ln
    return out


def test_back_to_work_what_if_with_three_overrides_and_a_diff(
    client: TestClient, imported: ImportResult, session: Session
) -> None:
    """Phase 2: create a "Back to work" what-if from live, override three lines, see the diff."""
    r = client.post(
        "/api/versions",
        json={
            "name": "Back to work",
            "base_version_id": imported.live.id,
            "planned_start": "2027-04-01",
        },
    )
    assert r.status_code == 201
    what_if = r.json()
    assert (what_if["type"], what_if["status"], what_if["planned_start"]) == (
        "what_if",
        "draft",
        "2027-04-01",
    )

    lines = _lines(client, what_if["id"])
    for name, amount in (("Fun Money", 40000), ("Petrol", 15000), ("Groceries", 65000)):
        line = next(
            ln
            for key, ln in lines.items()
            if key.startswith(name) and ln["values"]["account"] in ("person_2_rbs", "joint_billing")
        )
        assert (
            client.patch(
                f"/api/versions/{what_if['id']}/lines/{line['line_id']}",
                json={"changes": {"amount_pence": amount}},
            ).status_code
            == 204
        )

    diff = client.get(f"/api/versions/{what_if['id']}/diff").json()
    assert sorted((c["label"], c["fields"]["amount_pence"]) for c in diff["changes"]) == [
        ("Fun Money", [30000, 40000]),
        ("Groceries", [70000, 65000]),
        ("Petrol", [5000, 15000]),
    ]
    marked = {k: ln["origin"] for k, ln in _lines(client, what_if["id"]).items()}
    assert sum(origin == "modified" for origin in marked.values()) == 3
    # Live hasn't moved.
    assert _lines(client, imported.live.id)["Groceries"]["values"]["amount_pence"] == 70000
    # Every change was recorded against Person 1.
    person_1_writes = session.scalars(
        select(AuditLog.action).where(AuditLog.entity == "override", AuditLog.user_id.is_not(None))
    ).all()
    assert person_1_writes.count("modify") == 3


def test_live_edits_are_dated_from_today_by_default(
    client: TestClient, imported: ImportResult
) -> None:
    sky = _lines(client, imported.live.id)["TV (Sky)"]
    assert (
        client.patch(
            f"/api/versions/{imported.live.id}/lines/{sky['line_id']}",
            json={"changes": {"amount_pence": 10850}, "effective_from": "2026-10-15"},
        ).status_code
        == 204
    )
    before = client.get(f"/api/versions/{imported.live.id}/lines", params=PARAMS).json()
    assert (
        next(ln for ln in before if ln["line_id"] == sky["line_id"])["values"]["amount_pence"]
        == 10475
    )
    after = client.get(
        f"/api/versions/{imported.live.id}/lines", params={"as_of": "2026-10-15"}
    ).json()
    assert (
        next(ln for ln in after if ln["line_id"] == sky["line_id"])["values"]["amount_pence"]
        == 10850
    )


def test_adding_removing_and_reverting_lines(client: TestClient, imported: ImportResult) -> None:
    wid = imported.what_if.id
    added = client.post(
        f"/api/versions/{wid}/lines",
        json={
            "values": {
                "name": "Nursery",
                "kind": "bill",
                "account": "joint_billing",
                "amount_pence": 95000,
                "day_of_month": 1,
            }
        },
    )
    assert added.status_code == 201
    groceries = _lines(client, wid)["Groceries"]
    assert groceries["origin"] == "modified"
    assert (
        client.post(f"/api/versions/{wid}/lines/{groceries['line_id']}/revert").status_code == 204
    )
    lines = _lines(client, wid)
    assert lines["Nursery"]["origin"] == "added"
    assert lines["Groceries"]["values"]["amount_pence"] == 70000
    assert client.delete(f"/api/versions/{wid}/lines/{added.json()['line_id']}").status_code == 204
    assert "Nursery" not in _lines(client, wid)

    bad = client.post(
        f"/api/versions/{wid}/lines", json={"values": {"name": "No account", "kind": "bill"}}
    )
    assert bad.status_code == 422
    unknown = client.patch(
        f"/api/versions/{wid}/lines/{groceries['line_id']}", json={"changes": {"colour": "red"}}
    )
    assert unknown.status_code == 422


def test_settings_incomes_duplicate_rebase_archive(
    client: TestClient, imported: ImportResult, session: Session
) -> None:
    wid = imported.what_if.id
    assert (
        client.patch(f"/api/versions/{wid}/settings", json={"split_basis": "surplus"}).status_code
        == 200
    )
    assert client.get(f"/api/versions/{wid}/summary").json()["split_basis"] == "surplus"
    assert (
        client.put(
            f"/api/versions/{wid}/incomes/person_2", json={"net_monthly_pence": 250000}
        ).status_code
        == 204
    )
    assert (
        client.patch(
            f"/api/versions/{imported.live.id}/settings", json={"buffer_pence": None}
        ).status_code
        == 422
    )

    copy = client.post(
        f"/api/versions/{wid}/duplicate", json={"name": "Back to normal copy"}
    ).json()
    original = _lines(client, wid)
    duplicated = _lines(client, copy["id"])
    assert {k: v["values"] for k, v in duplicated.items()} == {
        k: v["values"] for k, v in original.items()
    }
    assert client.get(f"/api/versions/{copy['id']}/summary").json()["split_basis"] == "surplus"

    child = client.post(
        "/api/versions", json={"name": "Child", "base_version_id": copy["id"]}
    ).json()
    assert client.post(f"/api/versions/{copy['id']}/archive").status_code == 422  # has a child
    assert (
        client.post(
            f"/api/versions/{child['id']}/rebase", json={"base_version_id": imported.live.id}
        ).status_code
        == 200
    )
    assert client.post(f"/api/versions/{copy['id']}/archive").json()["status"] == "archived"
    assert (
        client.post(f"/api/versions/{wid}/rebase", json={"base_version_id": wid}).status_code == 422
    )


def test_promote_with_conflict_choice_then_undo(client: TestClient, imported: ImportResult) -> None:
    live_id, wid = imported.live.id, imported.what_if.id
    groceries = _lines(client, live_id)["Groceries"]
    client.patch(
        f"/api/versions/{live_id}/lines/{groceries['line_id']}",
        json={"changes": {"amount_pence": 75000}},
    )

    body: dict[str, Any] = {"effective_date": "2026-10-30"}
    preview = client.post(f"/api/versions/{wid}/promotion-preview", json=body).json()
    [conflict] = preview["conflicts"]
    assert conflict["label"] == "Groceries"
    assert client.post(f"/api/versions/{wid}/promote", json=body).status_code == 409

    body["resolutions"] = {str(conflict["override_id"]): "override"}
    promoted = client.post(f"/api/versions/{wid}/promote", json=body)
    assert promoted.status_code == 200
    assert promoted.json()["undoable"] is True
    latest = client.get("/api/promotions/latest").json()
    assert latest["what_if_name"] == "Back to normal"
    assert client.get(f"/api/versions/{live_id}").json()["name"] == "Back to normal"

    assert client.post(f"/api/promotions/{latest['id']}/undo").status_code == 204
    assert client.get(f"/api/versions/{live_id}").json()["name"] == "Maternity"
    assert client.get("/api/promotions/latest").json() is None


def test_a_payday_run_stops_promotion_undo(
    client: TestClient, imported: ImportResult, session: Session
) -> None:
    wid = imported.what_if.id
    promoted = client.post(
        f"/api/versions/{wid}/promote", json={"effective_date": "2026-10-30"}
    ).json()
    assert client.post("/api/payday/2026/10/start").status_code == 200
    assert client.get("/api/promotions/latest").json()["undoable"] is False
    refused = client.post(f"/api/promotions/{promoted['id']}/undo")
    assert refused.status_code == 422
    assert "payday" in refused.json()["detail"]


def test_periods_are_read_only(
    client: TestClient, imported: ImportResult, session: Session
) -> None:
    client.post(
        f"/api/versions/{imported.what_if.id}/promote", json={"effective_date": "2026-10-30"}
    )
    period = session.scalars(select(BudgetVersion).where(BudgetVersion.type == "period")).one()
    line_id = client.get(f"/api/versions/{period.id}/lines").json()[0]["line_id"]
    r = client.patch(
        f"/api/versions/{period.id}/lines/{line_id}", json={"changes": {"amount_pence": 1}}
    )
    assert r.status_code == 422


def test_archive_can_be_undone(client: TestClient, imported: ImportResult) -> None:
    wid = imported.what_if.id
    assert client.post(f"/api/versions/{wid}/archive").json()["status"] == "archived"
    restored = client.post(f"/api/versions/{wid}/restore")
    assert restored.status_code == 200
    assert restored.json()["status"] == "draft"
    assert client.post(f"/api/versions/{wid}/restore").status_code == 422  # not archived now
