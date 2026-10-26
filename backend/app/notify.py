"""Home Assistant notifications (PLAN.md Phase 5).

One POST of JSON to a Home Assistant webhook. HA's automation decides who hears it and how
(a phone notification, a light); `person` says whose it is, so Person 2's bank link warnings can
go to their phone. Sending never raises: a failure is logged and recorded, and the inbox still
holds everything.
"""

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger("bawbee.notify")


def send(
    settings: Settings,
    kind: str,
    title: str,
    message: str,
    *,
    person: str | None = None,
    data: dict[str, Any] | None = None,
    http: httpx.Client | None = None,
) -> str | None:
    """Post one notification. Returns None when sent, or what went wrong."""
    if not settings.ha_webhook_url:
        return "Home Assistant isn't set up (BAWBEE_HA_WEBHOOK_URL)."
    body = {
        "source": "bawbee",
        "kind": kind,
        "title": title,
        "message": message,
        "person": person,
        "url": settings.public_url,
        "data": data or {},
    }
    client = http or httpx.Client(timeout=10)
    try:
        response = client.post(settings.ha_webhook_url, json=body)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Home Assistant notification %s failed: %s", kind, exc)
        return f"Home Assistant didn't take it: {exc}"
    finally:
        if http is None:
            client.close()
    return None
