import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from app import __version__
from app.api import (
    auth,
    banks,
    editing,
    export,
    ha,
    inbox,
    meta,
    payday,
    plan,
    reference,
    transactions,
    versions,
)
from app.config import Settings, get_settings

logger = logging.getLogger("bawbee")


class SpaStaticFiles(StaticFiles):
    """Serve the built frontend, falling back to index.html for client-side routes."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path == "api" or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="A Wee Bawbee", version=__version__, docs_url="/api/docs")

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, Any]:
        return {"status": "ok", "app": "bawbee", "version": __version__}

    for module in (
        auth,
        versions,
        editing,
        payday,
        reference,
        export,
        transactions,
        inbox,
        banks,
        plan,
    ):
        app.include_router(module.router)
    app.include_router(banks.public)  # the bank's redirect back, before sign-in
    app.include_router(ha.router)
    app.include_router(ha.public)  # Home Assistant's sensor, with its own token
    app.include_router(meta.router)  # which build is running, public like /health

    static_dir = Path(settings.static_dir)
    if (static_dir / "index.html").is_file():
        app.mount("/", SpaStaticFiles(directory=static_dir, html=True), name="frontend")
    else:
        logger.warning("No built frontend at %s; serving API only", static_dir)

    return app


app = create_app()
