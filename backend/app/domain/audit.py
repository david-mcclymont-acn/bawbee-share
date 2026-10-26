"""Audit trail: every write is recorded with who made it (PLAN.md §13)."""

from typing import Any

from sqlalchemy.orm import Session

from app.domain.models import AuditLog, User


def record(
    session: Session,
    actor: User | None,
    entity: str,
    entity_id: int | None,
    action: str,
    before: Any = None,
    after: Any = None,
) -> None:
    session.add(
        AuditLog(
            user_id=actor.id if actor else None,
            entity=entity,
            entity_id=entity_id,
            action=action,
            before_json=before,
            after_json=after,
        )
    )
