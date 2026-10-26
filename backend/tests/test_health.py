import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.worker import build_scheduler, healthcheck, heartbeat


def test_health(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(static_dir=tmp_path)))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_serves_frontend_with_spa_fallback(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Bawbee</title>")
    client = TestClient(create_app(Settings(static_dir=tmp_path)))

    assert "Bawbee" in client.get("/").text
    assert "Bawbee" in client.get("/payday").text
    assert client.get("/health").json()["status"] == "ok"


def test_worker_schedules_heartbeat_bank_sync_and_alerts() -> None:
    jobs = {job.id: job for job in build_scheduler().get_jobs()}
    assert set(jobs) == {"heartbeat", "bank_sync", "alerts"}
    # Twice a day, UK time: banks allow only a few unattended syncs.
    assert str(jobs["bank_sync"].trigger) == "cron[hour='6,18', minute='0']"
    assert str(jobs["alerts"].trigger) == "cron[hour='7', minute='0']"


def test_worker_healthcheck_tracks_heartbeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    beat = tmp_path / "worker.heartbeat"
    monkeypatch.setenv("BAWBEE_WORKER_HEARTBEAT_FILE", str(beat))
    get_settings.cache_clear()
    try:
        assert healthcheck() == 1  # no heartbeat yet
        heartbeat()
        assert healthcheck() == 0
        stale = time.time() - 3600
        os.utime(beat, (stale, stale))
        assert healthcheck() == 1
    finally:
        get_settings.cache_clear()
