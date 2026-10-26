"""Which build of the app is running (not budget versions: those are /api/versions).

Public, like /health: it says only which commit is deployed, so a check can tell whether
the live app is on the latest main before anyone chases a bug that's already fixed.
"""

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__, build
from app.api.deps import SessionDep

router = APIRouter(tags=["meta"])


@router.get("/api/version")
def version(session: SessionDep) -> dict[str, Any]:
    info = build.build_info()
    commit = info["commit"]
    return {
        "app": "bawbee",
        "version": __version__,
        "commit": commit,
        "commit_short": commit[:7] if commit else None,
        "built_at": info["built_at"],
        # The database's migration, so a deploy whose migration didn't run shows up too.
        "schema_revision": session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar(),
    }
