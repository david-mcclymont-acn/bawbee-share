import io
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import main
from app.config import get_settings
from app.db import make_engine
from app.domain import auth
from app.domain.models import User
from tests.conftest import SEED_PATH


@pytest.fixture
def db_url(migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    path = tmp_path / "cli.db"
    shutil.copy(migrated_db, path)
    url = f"sqlite:///{path}"
    monkeypatch.setenv("BAWBEE_DATABASE_URL", url)
    get_settings.cache_clear()
    yield url
    get_settings.cache_clear()


def _users(url: str) -> list[User]:
    engine = make_engine(url)
    with Session(engine, expire_on_commit=False) as s:
        users = list(s.scalars(select(User).order_by(User.id)))
    engine.dispose()
    return users


def test_import_then_create_and_manage_users(
    db_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["import-seed", str(SEED_PATH), "--as-of", "2026-10-01"])
    assert "Imported 55 live lines" in capsys.readouterr().out

    monkeypatch.setattr("sys.stdin", io.StringIO("a long enough password\n"))
    main(["user", "create", "PERSON_1", "--person", "person_1", "--password-stdin"])
    [person_1] = _users(db_url)
    assert (person_1.username, person_1.display_name) == ("person_1", "Person 1")
    assert (person_1.password_hash or "").startswith("$argon2id$")

    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    with pytest.raises(SystemExit, match="at least 12"):
        main(["user", "create", "person_2", "--person", "person_2", "--password-stdin"])

    # PowerShell pipes a byte-order mark and "\r\n"; neither is part of the password.
    monkeypatch.setattr("sys.stdin", io.StringIO("\ufeffanother long password\r\n"))
    main(["user", "reset-password", "person_1", "--password-stdin"])
    engine = make_engine(db_url)
    with Session(engine) as s:
        assert (
            auth.authenticate(s, "person_1", "another long password", None).username == "person_1"
        )
    engine.dispose()

    main(["user", "disable", "person_1"])
    assert _users(db_url)[0].disabled_at is not None
    main(["user", "list"])
    assert "disabled" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="No user called"):
        main(["user", "enable", "fergus"])
