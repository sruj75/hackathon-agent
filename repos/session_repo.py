"""
Session repository implementation with Supabase Postgres.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from db import get_pool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_jsonb_param(value: object) -> str:
    """Serialize JSONB parameters for asyncpg."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


async def save_session(
    session_id: str,
    state: dict,
    user_id: Optional[str] = None,
    date: Optional[str] = None,
) -> dict:
    """Save or update session state."""
    u_id = user_id or state.get("user_id")
    d_str = date or state.get("date", datetime.now().strftime("%Y-%m-%d"))
    if not u_id:
        raise ValueError("user_id required in state or args for new session")
    return await upsert_session(session_id=session_id, user_id=u_id, date=d_str, state=state)


async def get_session(session_id: str) -> Optional[dict]:
    """Get session by session_id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM sessions WHERE session_id = $1",
            session_id,
        )
    return dict(row) if row else None


async def get_today_session(user_id: str, date: str) -> Optional[dict]:
    """Get session for user on specific date."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT *
            FROM sessions
            WHERE user_id = $1 AND date = $2
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            user_id,
            date,
        )
    return dict(row) if row else None


async def upsert_session(session_id: str, user_id: str, date: str, state: dict) -> dict:
    """Create or update session with explicit fields."""
    now = _utcnow()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO sessions (session_id, user_id, date, state, created_at, updated_at)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6)
            ON CONFLICT (session_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                date = EXCLUDED.date,
                state = EXCLUDED.state,
                updated_at = EXCLUDED.updated_at
            RETURNING *
            """,
            session_id,
            user_id,
            date,
            _as_jsonb_param(state),
            now,
            now,
        )
    return dict(row)
