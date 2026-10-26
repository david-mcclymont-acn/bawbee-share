"""Exports: JSON backup and the Excel workbook (PLAN.md §11.3 Settings)."""

import json

from fastapi import APIRouter, Depends, Response

from app.api.deps import SessionDep, UserDep, current_session
from app.api.versions import today
from app.domain import audit
from app.exporters.backup import backup
from app.exporters.xlsx import workbook

router = APIRouter(prefix="/api/export", tags=["export"], dependencies=[Depends(current_session)])


def _attachment(filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    }


@router.get("/backup.json")
def backup_json(user: UserDep, session: SessionDep) -> Response:
    body = json.dumps(backup(session), indent=2, ensure_ascii=False)
    audit.record(session, user, "export", None, "backup_json")
    session.commit()
    return Response(
        body,
        media_type="application/json",
        headers=_attachment(f"bawbee-backup-{today().isoformat()}.json"),
    )


@router.get("/budget.xlsx")
def budget_xlsx(user: UserDep, session: SessionDep) -> Response:
    when = today()
    data = workbook(session, when)
    audit.record(session, user, "export", None, "budget_xlsx")
    session.commit()
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=_attachment(f"bawbee-budget-{when.isoformat()}.xlsx"),
    )
