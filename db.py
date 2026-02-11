"""
Async Postgres client for Supabase-backed persistence.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Return a lazily initialized asyncpg pool."""
    global _pool
    if _pool is not None:
        return _pool

    db_url = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError(
            "Missing database URL. Set SUPABASE_DB_URL (or DATABASE_URL)."
        )

    _pool = await asyncpg.create_pool(
        dsn=db_url,
        min_size=1,
        max_size=10,
        command_timeout=30,
    )
    logger.info("Supabase Postgres pool initialized")
    return _pool


async def close_pool() -> None:
    """Close pool during shutdown/tests."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
