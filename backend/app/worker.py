"""Background worker (bawbee-worker): the heartbeat, bank syncs and alerts.

Banks allow only a few unattended syncs a day, so every connection syncs at the hours in
BAWBEE_SYNC_HOURS (06:00 and 18:00 UK time by default). After each sync, and each morning at
07:00, the alerts pass runs: recurring payments, missed payments, bills still paid after a
"cancel", consents ending, then one batched Home Assistant notification (sync/alerts.py).
"""

import logging
import sys
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import make_engine
from app.sync import alerts, banks

logger = logging.getLogger("bawbee.worker")


def heartbeat() -> None:
    get_settings().worker_heartbeat_file.touch()
    logger.info("worker alive")


_engine: Engine | None = None


def _session() -> Session:
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings().database_url)
    return Session(_engine, expire_on_commit=False)


def sync_job() -> None:
    with _session() as session:
        runs = banks.sync_all(session, get_settings())
        session.commit()
    alerts_job()
    for run in runs:
        logger.info(
            "sync connection=%s status=%s new=%s updated=%s removed=%s",
            run.connection_id,
            run.status,
            run.rows_new,
            run.rows_updated,
            run.rows_removed,
        )


def alerts_job() -> None:
    with _session() as session:
        found = alerts.run(session, get_settings(), banks.uk_today())
        session.commit()
    logger.info(
        "alerts still_paying=%s missed=%s recurring_new=%s consents=%s notified=%s",
        found.still_paying,
        found.missed,
        found.recurring.proposed,
        found.consents,
        found.notified,
    )


def healthcheck() -> int:
    """Exit status for the container healthcheck: 0 if the heartbeat is fresh."""
    settings = get_settings()
    try:
        age = time.time() - settings.worker_heartbeat_file.stat().st_mtime
    except FileNotFoundError:
        return 1
    return 0 if age < settings.worker_heartbeat_seconds * 2 + 60 else 1


def build_scheduler() -> BlockingScheduler:
    settings = get_settings()
    scheduler = BlockingScheduler(timezone="Europe/London")
    scheduler.add_job(
        heartbeat, "interval", seconds=settings.worker_heartbeat_seconds, id="heartbeat"
    )
    scheduler.add_job(
        sync_job,
        "cron",
        hour=settings.sync_hours,
        minute=0,
        id="bank_sync",
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.add_job(
        alerts_job,
        "cron",
        hour=7,
        minute=0,
        id="alerts",
        misfire_grace_time=3600,
        coalesce=True,
    )
    return scheduler


def main() -> None:
    logging.basicConfig(
        level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    scheduler = build_scheduler()
    logger.info("starting scheduler with jobs: %s", [job.id for job in scheduler.get_jobs()])
    heartbeat()
    scheduler.start()


if __name__ == "__main__":
    if sys.argv[1:] == ["healthcheck"]:
        sys.exit(healthcheck())
    main()
