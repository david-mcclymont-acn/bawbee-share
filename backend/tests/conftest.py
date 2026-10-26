import json
import shutil
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session, make_engine
from app.domain.auth import create_user
from app.domain.models import User
from app.importers.seed import ImportResult, import_seed
from app.main import create_app

BACKEND = Path(__file__).resolve().parents[1]
SEED_PATH = BACKEND.parent / "seed" / "budget-seed.json"
AS_OF = date(2026, 10, 1)


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logging"] = False
    return config


@pytest.fixture(scope="session")
def seed() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return data


@pytest.fixture(scope="session")
def migrated_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """An empty database at the latest migration, copied fresh for each test."""
    path = tmp_path_factory.mktemp("db") / "template.db"
    command.upgrade(alembic_config(f"sqlite:///{path}"), "head")
    # Fold the WAL into the main file so a plain file copy carries the schema.
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
    return path


@pytest.fixture
def session(migrated_db: Path, tmp_path: Path) -> Iterator[Session]:
    path = tmp_path / "test.db"
    shutil.copy(migrated_db, path)
    engine = make_engine(f"sqlite:///{path}")
    with Session(engine, expire_on_commit=False) as s:
        yield s
    engine.dispose()


@pytest.fixture
def raw_import(session: Session, seed: dict[str, Any]) -> ImportResult:
    """The seed exactly as the spreadsheet has it (no post-import cleanups)."""
    result = import_seed(session, seed, AS_OF, apply_cleanups=False)
    session.commit()
    return result


@pytest.fixture
def imported(session: Session, seed: dict[str, Any]) -> ImportResult:
    """The seed with post-import cleanups applied."""
    result = import_seed(session, seed, AS_OF)
    session.commit()
    return result


# ---- the web app, signed in ------------------------------------------------------------

PASSWORD = "a wee bawbee for the bairn"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(static_dir=tmp_path, cookie_secure=False, public_url="http://testserver")


@pytest.fixture
def app(session: Session, settings: Settings) -> FastAPI:
    application = create_app(settings)

    def _session() -> Iterator[Session]:
        yield session

    application.dependency_overrides[get_session] = _session
    application.dependency_overrides[get_settings] = lambda: settings
    return application


@pytest.fixture
def users(session: Session, imported: ImportResult) -> dict[str, User]:
    """Person 1 and Person 2, each with a password (and the cleaned seed imported)."""
    made = {
        key: create_user(
            session,
            key,
            key.replace("_", " ").title(),
            key,
            PASSWORD,
            None,
            email=f"{key}@example.com",
        )
        for key in ("person_1", "person_2")
    }
    session.commit()
    return made


def login(app: FastAPI, username: str, password: str = PASSWORD) -> TestClient:
    """A browser signed in as `username`, sending its CSRF token on every request."""
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


@pytest.fixture
def client(app: FastAPI, users: dict[str, User]) -> TestClient:
    return login(app, "person_1")
