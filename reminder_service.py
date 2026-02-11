"""
Reminder scheduling helpers for automated push notifications.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
import os
from zoneinfo import ZoneInfo

import cron_service
from repos import event_repo

logger = logging.getLogger(__name__)

DEFAULT_LEAD_MINUTES = 5
IMMEDIATE_DELAY_SECONDS = 10


def reminders_enabled() -> bool:
    """Feature gate for automated event reminders."""
    return os.getenv("ENABLE_AUTOMATED_EVENT_REMINDERS", "true").lower() in (
        "1",
        "true",
        "yes",
    )


def _coerce_event_start_time(
    event_start_time: datetime | str,
    tz_name: str,
) -> datetime:
    tz = ZoneInfo(tz_name)
    if isinstance(event_start_time, datetime):
        if event_start_time.tzinfo is None:
            return event_start_time.replace(tzinfo=tz)
        return event_start_time.astimezone(tz)

    parsed = datetime.fromisoformat(event_start_time.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


async def schedule_calendar_reminder(
    *,
    user_id: str,
    calendar_event_id: str,
    event_title: str,
    event_start_time: datetime | str,
    timezone_name: str,
    lead_minutes: int = DEFAULT_LEAD_MINUTES,
    source: str = "agent_timeblock",
) -> dict:
    """Create or reschedule a single pending reminder row for a calendar event."""
    if not reminders_enabled():
        return {"status": "disabled"}

    start_local = _coerce_event_start_time(event_start_time, timezone_name)
    now_local = datetime.now(ZoneInfo(timezone_name))
    reminder_at = start_local - timedelta(minutes=lead_minutes)

    immediate = False
    if reminder_at <= now_local:
        reminder_at = now_local + timedelta(seconds=IMMEDIATE_DELAY_SECONDS)
        immediate = True

    payload = {
        "calendar_event_id": calendar_event_id,
        "event_title": event_title,
        "event_start_time": start_local.isoformat(),
        "timezone": timezone_name,
        "lead_minutes": lead_minutes,
        "source": source,
        "schedule_owner": "system",
        "schedule_policy": "calendar_reminder",
    }

    pending = await event_repo.list_pending_by_calendar_event(user_id, calendar_event_id)
    primary = pending[0] if pending else None
    extras = pending[1:] if len(pending) > 1 else []

    for row in extras:
        cron_job_id = row.get("cron_job_id")
        if cron_job_id:
            try:
                await cron_service.delete_job(cron_job_id)
            except Exception as cleanup_error:
                logger.warning(
                    "[reminder] Failed to cleanup duplicate cron job %s: %s",
                    cron_job_id,
                    cleanup_error,
                )

    if extras:
        await event_repo.mark_cancelled(
            [str(row["id"]) for row in extras if row.get("id")],
            reason="superseded_by_reschedule",
        )

    if primary:
        reminder_event_id = str(primary["id"])
        previous_cron_job_id = primary.get("cron_job_id")
        if previous_cron_job_id:
            try:
                await cron_service.delete_job(previous_cron_job_id)
            except Exception as cleanup_error:
                logger.warning(
                    "[reminder] Failed to cleanup previous cron job %s: %s",
                    previous_cron_job_id,
                    cleanup_error,
                )

        cron_job_id = await cron_service.create_one_time_job(
            target_datetime=reminder_at,
            event_id=reminder_event_id,
            timezone=timezone_name,
        )
        await event_repo.update_event(
            reminder_event_id,
            scheduled_time=reminder_at,
            payload=payload,
            executed=False,
            cron_job_id=cron_job_id,
            last_error=None,
            last_attempt_at=None,
        )
    else:
        created = await event_repo.create_event(
            user_id=user_id,
            scheduled_time=reminder_at,
            event_type="calendar_reminder",
            payload=payload,
        )
        reminder_event_id = str(created["id"])
        cron_job_id = await cron_service.create_one_time_job(
            target_datetime=reminder_at,
            event_id=reminder_event_id,
            timezone=timezone_name,
        )
        await event_repo.update_cron_job_id(reminder_event_id, cron_job_id)

    logger.info(
        "[reminder] Scheduled user=%s calendar_event_id=%s event_id=%s at=%s immediate=%s",
        user_id,
        calendar_event_id,
        reminder_event_id,
        reminder_at.isoformat(),
        immediate,
    )
    return {
        "status": "scheduled",
        "event_id": reminder_event_id,
        "cron_job_id": cron_job_id,
        "scheduled_time": reminder_at.isoformat(),
        "immediate": immediate,
    }


async def cancel_calendar_reminders(
    *,
    user_id: str,
    calendar_event_id: str,
    reason: str = "calendar_event_deleted",
) -> dict:
    """Cancel all pending reminders for a calendar event."""
    pending = await event_repo.list_pending_by_calendar_event(user_id, calendar_event_id)
    if not pending:
        return {"status": "noop", "cancelled": 0}

    for row in pending:
        cron_job_id = row.get("cron_job_id")
        if cron_job_id:
            try:
                await cron_service.delete_job(cron_job_id)
            except Exception as cleanup_error:
                logger.warning(
                    "[reminder] Failed to cleanup cron job %s while cancelling: %s",
                    cron_job_id,
                    cleanup_error,
                )

    cancelled = await event_repo.mark_cancelled(
        [str(row["id"]) for row in pending if row.get("id")],
        reason=reason,
    )
    logger.info(
        "[reminder] Cancelled user=%s calendar_event_id=%s rows=%s",
        user_id,
        calendar_event_id,
        cancelled,
    )
    return {"status": "cancelled", "cancelled": cancelled}
