"""
Supabase pg_cron scheduler adapter.

This module keeps the legacy create/delete interface used across the app,
but the implementation now uses Postgres functions backed by pg_cron.
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
import logging
import os

from repos import event_repo

logger = logging.getLogger(__name__)

# Backward-compatible constants kept for test patching only.
CRONJOB_API_KEY = os.getenv("CRONJOB_ORG_API_KEY")
BACKEND_URL = os.getenv("BACKEND_URL") or os.getenv("RENDER_EXTERNAL_URL")


def _coerce_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt_timezone.utc)
    return value.astimezone(dt_timezone.utc)


async def create_one_time_job(
    target_datetime: datetime,
    event_id: str,
    timezone: str,
) -> int:
    """
    Schedule one event reminder using pg_cron and return job id.
    """
    if not isinstance(timezone, str) or not timezone.strip():
        raise ValueError("missing_timezone")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("missing_event_id")

    run_at_utc = _coerce_aware_utc(target_datetime)
    job_id = await event_repo.schedule_event_job(
        event_id=event_id,
        run_at=run_at_utc,
        timezone_name=timezone,
    )
    logger.info(
        "Created pg_cron job %s for event %s at %s (%s)",
        job_id,
        event_id,
        run_at_utc.isoformat(),
        timezone,
    )
    return int(job_id)


async def delete_job(job_id: int | None) -> bool:
    """
    Unschedule an existing pg_cron job by job id.
    """
    if not job_id:
        logger.warning("No job_id provided, skipping deletion")
        return False

    removed = await event_repo.unschedule_event_job(int(job_id))
    if removed:
        logger.info("Deleted pg_cron job %s", job_id)
    else:
        logger.warning("Failed to delete pg_cron job %s", job_id)
    return removed
