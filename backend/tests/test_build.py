"""Which build is running: the commit read from .git at build time, and GET /api/version."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import build

SHA = "0123456789abcdef0123456789abcdef01234567"


def _head() -> str:
    """The latest migration: what a migrated database reports."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(Path(build.__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(build.__file__).resolve().parents[1] / "migrations")
    )
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


def test_the_commit_comes_from_a_branch_a_packed_ref_or_a_detached_head(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    assert build.commit_from_git_dir(git) is None  # no such branch yet

    (git / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{SHA} refs/heads/main\n", encoding="utf-8"
    )
    assert build.commit_from_git_dir(git) == SHA

    newer = "f" * 40
    (git / "refs" / "heads" / "main").write_text(newer + "\n", encoding="utf-8")
    assert build.commit_from_git_dir(git) == newer  # a loose ref is newer than packed-refs

    (git / "HEAD").write_text(SHA + "\n", encoding="utf-8")  # CI checks out a commit
    assert build.commit_from_git_dir(git) == SHA

    assert build.commit_from_git_dir(tmp_path / "nowhere") is None


def test_the_build_file_records_the_commit_and_when(tmp_path: Path) -> None:
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(SHA, encoding="utf-8")
    out = tmp_path / "build.json"
    build.write_build_file(git, out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["commit"] == SHA
    assert written["built_at"].endswith("+00:00")


def test_the_docker_build_can_run_it_on_its_own(tmp_path: Path) -> None:
    """As the Dockerfile does: the file copied out alone, run with no app package around it.

    (In the image it sits at /build.py; anything worked out from its parents at import
    time broke the build, so build_file() is only worked out when the app asks.)
    """
    import shutil
    import subprocess
    import sys

    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(SHA, encoding="utf-8")
    alone = tmp_path / "build.py"
    shutil.copy(build.__file__, alone)
    out = tmp_path / "build.json"
    subprocess.run([sys.executable, "-I", str(alone), str(git), str(out)], check=True)  # noqa: S603
    assert json.loads(out.read_text(encoding="utf-8"))["commit"] == SHA


def test_version_says_which_commit_runs_without_signing_in(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        build, "build_info", lambda: {"commit": SHA, "built_at": "2026-10-23T09:00:00+00:00"}
    )
    anonymous = TestClient(app)
    got = anonymous.get("/api/version")
    assert got.status_code == 200
    assert got.json() == {
        "app": "bawbee",
        "version": got.json()["version"],
        "commit": SHA,
        "commit_short": SHA[:7],
        "built_at": "2026-10-23T09:00:00+00:00",
        "schema_revision": _head(),
    }
