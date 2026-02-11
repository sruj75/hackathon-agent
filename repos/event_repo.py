"""
Event repository implementation with Supabase Postgres.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import List, Optional
import uuid

from db import get_pool

logger = logging.getLogger(__name__)

_UPDATE_COLUMNS = {
    "user_id",
    "scheduled_time",
    "event_type",
    "payload",
    "executed",
    "cron_job_id",
    "last_error",
    "last_attempt_at",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_event(
    user_id: str,
    scheduled_time: datetime,
    event_type: str,
    payload: dict,
    cron_job_id: Optional[int] = None,
) -> dict:
    """Create a new scheduled event."""
    event_id = str(uuid.uuid4())
    now = _utcnow()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO events (
                id, user_id, scheduled_time, event_type, payload, executed,
                cron_job_id, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
            RETURNING *
            """,
            event_id,
            user_id,
            scheduled_time,
            event_type,
            payload,
            False,
            cron_job_id,
            now,
            now,
        )
    return dict(row)


async def update_cron_job_id(event_id: str, cron_job_id: int) -> bool:
    """Update the cron_job_id for an event after creating the cron job."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE events SET cron_job_id = $2, updated_at = $3 WHERE id = $1",
            event_id,
            cron_job_id,
            _utcnow(),
        )
    return result.endswith("1")


async def update_event(event_id: str, **kwargs) -> bool:
    """Update arbitrary fields for an event."""
    update_data = {k: v for k, v in kwargs.items() if k in _UPDATE_COLUMNS}
    if not update_data:
        return False

    set_clauses = []
    values = []
    index = 2
    for column, value in update_data.items():
        if column == "payload":
            set_clauses.append(f"{column} = ${index}::jsonb")
        else:
            set_clauses.append(f"{column} = ${index}")
        values.append(value)
        index += 1

    set_clauses.append(f"updated_at = ${index}")
    values.append(_utcnow())
    query = f"UPDATE events SET {', '.join(set_clauses)} WHERE id = $1"

    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(query, event_id, *values)
    return result.endswith("1")


async def mark_executed(event_id: str) -> None:
    """Mark an event as executed."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE events SET executed = TRUE, updated_at = $2 WHERE id = $1",
            event_id,
            _utcnow(),
        )


async def get_event_by_type_and_time(
    user_id: str, event_type: str, scheduled_time: datetime
) -> Optional[dict]:
    """Get event by user_id, event_type, and scheduled_time."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM events
            WHERE user_id = $1 AND event_type = $2 AND scheduled_time = $3
            LIMIT 1
            """,
            user_id,
            event_type,
            scheduled_time,
        )
    return dict(row) if row else None


async def get_by_id(event_id: str) -> Optional[dict]:
    """Get event by event_id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM events WHERE id = $1", event_id)
    return dict(row) if row else None


async def find_pending_morning_event(user_id: str, seed_date: str) -> Optional[dict]:
    """
    Find an unexecuted morning_wake event for a user's local date key.

    seed_date is a YYYY-MM-DD string in the user's timezone.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM events
            WHERE user_id = $1
              AND event_type = 'morning_wake'
              AND executed = FALSE
              AND payload->>'seed_date' = $2
            ORDER BY scheduled_time ASC
            LIMIT 1
            """,
            user_id,
            seed_date,
        )
    return dict(row) if row else None


async def list_future_unexecuted_events_missing_cron(limit: int = 200) -> List[dict]:
    """
    Return future unexecuted events that do not yet have cron_job_id assigned.
    Used to reconcile missed cron scheduling after transient failures.
    """
    now_utc = datetime.now(timezone.utc)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *
            FROM events
            WHERE executed = FALSE
              AND cron_job_id IS NULL
              AND scheduled_time > $1
            ORDER BY scheduled_time ASC
            LIMIT $2
            """,
            now_utc,
            limit,
        )
    return [dict(row) for row in rows]
