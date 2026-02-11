"""
User repository implementation with Supabase Postgres.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from db import get_pool

_PROFILE_COLUMNS = {"wake_time", "bedtime", "timezone", "health_anchors"}


def _row_to_dict(row) -> dict:
    return dict(row) if row is not None else {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_profile(
    user_id: str,
    wake_time: str,
    bedtime: str,
    timezone: str | None = None,
    health_anchors: Optional[List[str]] = None,
) -> dict:
    """Create a new user profile."""
    now = _utcnow()
    profile_data = {
        "user_id": user_id,
        "wake_time": wake_time,
        "bedtime": bedtime,
        "timezone": timezone,
        "health_anchors": health_anchors or [],
        "created_at": now,
        "updated_at": now,
    }

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (
                user_id, wake_time, bedtime, timezone, health_anchors, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
            ON CONFLICT (user_id) DO UPDATE SET
                wake_time = EXCLUDED.wake_time,
                bedtime = EXCLUDED.bedtime,
                timezone = EXCLUDED.timezone,
                health_anchors = EXCLUDED.health_anchors,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            user_id,
            wake_time,
            bedtime,
            timezone,
            health_anchors or [],
            now,
            now,
        )
    return _row_to_dict(row)


async def get_profile(user_id: str) -> Optional[dict]:
    """Get user profile by user_id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
    return _row_to_dict(row) if row else None


async def update_profile(user_id: str, **kwargs) -> dict:
    """Update or create user profile."""
    filtered_updates = {k: v for k, v in kwargs.items() if k in _PROFILE_COLUMNS}
    now = _utcnow()
    pool = await get_pool()

    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if existing:
            if filtered_updates:
                set_clauses = []
                values = []
                index = 2
                for column, value in filtered_updates.items():
                    if column == "health_anchors":
                        set_clauses.append(f"{column} = ${index}::jsonb")
                    else:
                        set_clauses.append(f"{column} = ${index}")
                    values.append(value)
                    index += 1
                set_clauses.append(f"updated_at = ${index}")
                values.append(now)
                query = (
                    f"UPDATE users SET {', '.join(set_clauses)} WHERE user_id = $1"
                )
                await conn.execute(query, user_id, *values)
            else:
                await conn.execute(
                    "UPDATE users SET updated_at = $2 WHERE user_id = $1",
                    user_id,
                    now,
                )
            row = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            return _row_to_dict(row)

        insert_payload = {
            "user_id": user_id,
            "wake_time": filtered_updates.get("wake_time"),
            "bedtime": filtered_updates.get("bedtime"),
            "timezone": filtered_updates.get("timezone"),
            "health_anchors": filtered_updates.get("health_anchors", []),
            "created_at": now,
            "updated_at": now,
        }
        row = await conn.fetchrow(
            """
            INSERT INTO users (
                user_id, wake_time, bedtime, timezone, health_anchors, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
            RETURNING *
            """,
            insert_payload["user_id"],
            insert_payload["wake_time"],
            insert_payload["bedtime"],
            insert_payload["timezone"],
            insert_payload["health_anchors"],
            insert_payload["created_at"],
            insert_payload["updated_at"],
        )
        return _row_to_dict(row)


async def get_push_token(user_id: str) -> Optional[str]:
    """Get Expo push token for user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT expo_push_token FROM push_tokens WHERE user_id = $1", user_id
        )
    return row["expo_push_token"] if row else None


async def save_push_token(user_id: str, token: str) -> dict:
    """Save or update user's Expo push token."""
    now = _utcnow()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO push_tokens (user_id, expo_push_token, created_at, updated_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE SET
                expo_push_token = EXCLUDED.expo_push_token,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            user_id,
            token,
            now,
            now,
        )
    return _row_to_dict(row)


async def get_all_users() -> List[dict]:
    """Get all user profiles."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM users ORDER BY created_at ASC")
    return [dict(row) for row in rows]


async def delete_push_token(user_id: str) -> None:
    """Delete push token for user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM push_tokens WHERE user_id = $1", user_id)
