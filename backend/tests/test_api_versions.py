"""Reading versions over the API (signed in as Person 1)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.importers.seed import ImportResult
from tests.conftest import AS_OF

PARAMS = {"as_of": AS_OF.isoformat()}


def test_the_api_needs_a_signed_in_user(app: FastAPI, imported: ImportResult) -> None:
    anonymous = TestClient(app)
    assert anonymous.get("/api/versions").status_code == 401
    assert anonymous.get(f"/api/versions/{imported.live.id}/summary").status_code == 401
    assert anonymous.get("/health").status_code == 200


def test_list_versions(client: TestClient) -> None:
    body = client.get("/api/versions").json()
    assert [(v["name"], v["type"], v["status"]) for v in body] == [
        ("Maternity", "live", "active"),
        ("Back to normal", "what_if", "draft"),
    ]
    assert body[1]["base_version_id"] == body[0]["id"]


def test_summary_of_the_live_budget(client: TestClient, imported: ImportResult) -> None:
    r = client.get(f"/api/versions/{imported.live.id}/summary", params=PARAMS)
    assert r.status_code == 200
    body = r.json()
    assert body["version"]["name"] == "Maternity"
    assert body["split_basis"] == "take_home"
    # Apple Fitness+ has left Joint Billing (decision 16): £3,307.50 - £9.99.
    assert body["joint_total_pence"] == 329751
    person_1 = next(
        t
        for t in body["transfers"]
        if t["rule"] == "joint_share" and t["from_account"] == "person_1_monzo"
    )
    assert (person_1["actual_pence"], person_1["effective_pence"]) == (267000, 267000)
    assert person_1["difference_pence"] == 267000 - person_1["suggested_pence"]
    assert body["surpluses"] == {
        "personal": {"person_1": 51080, "person_2": -13005},
        "joint": 7249,
        "household": 45324,
    }
    assert body["shares"]["person_1"]["exact"] == "84316/114791"  # 421580 / 573955
    assert body["shares"]["person_1"]["percent"] == "73.4518"
    assert set(body["split_options"]) == {"gross", "take_home", "surplus"}
    assert {w["code"] for w in body["warnings"]} >= {"personal_shortfall", "manual_transfer"}
    joint = next(a for a in body["accounts"] if a["key"] == "joint_billing")
    assert joint["inflow_pence"] - joint["outflow_pence"] == 7249


def test_lines_and_diff(client: TestClient, imported: ImportResult) -> None:
    lines = client.get(f"/api/versions/{imported.what_if.id}/lines", params=PARAMS).json()
    origins = {ln["values"]["name"]: ln["origin"] for ln in lines}
    assert origins["HayU"] == "added"
    assert origins["Groceries"] == "modified"
    assert origins["Gas/Electricity"] == "base"
    ring = next(ln for ln in lines if ln["values"]["name"] == "Ring")
    assert ring["monthly_pence"] == 417

    d = client.get(f"/api/versions/{imported.what_if.id}/diff", params=PARAMS).json()
    assert d["base_version_id"] == imported.live.id
    assert {c["change"] for c in d["changes"]} == {"added", "removed", "modified"}
    assert d["conflicts"] == []
    assert d["redundant_override_ids"] == []

    assert client.get(f"/api/versions/{imported.live.id}/diff").status_code == 422


def test_joint_projection_opens_with_the_payday_transfers(
    client: TestClient, imported: ImportResult
) -> None:
    p = client.get(
        f"/api/versions/{imported.live.id}/projection", params={"year": 2026, "month": 10}
    ).json()
    assert (p["payday_month"], p["bill_month"]) == ("2026-10-01", "2026-11-01")
    assert p["opening_pence"] == 267000 + 70000
    assert p["minimum_pence"] == 7249
    assert p["goes_negative"] is False
    assert len(p["days"]) == 30
    assert p["days"][-1]["closing_pence"] > p["minimum_pence"]  # the next payday has landed


def test_unknown_version_is_404(client: TestClient) -> None:
    assert client.get("/api/versions/999/summary").status_code == 404
    assert client.get("/api/nope").status_code == 404
