"""Unit tests for db.py connection pool lifecycle."""

from unittest.mock import AsyncMock

import pytest

import db


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_get_pool_raises_when_db_url_missing(monkeypatch):
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    create_pool_mock = AsyncMock()
    monkeypatch.setattr(db.asyncpg, "create_pool", create_pool_mock)

    with pytest.raises(ValueError, match="Missing database URL"):
        await db.get_pool()

    create_pool_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_pool_initializes_once_and_caches(monkeypatch):
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setenv("SUPABASE_DB_URL", "postgres://user:pass@localhost:5432/testdb")

    fake_pool = object()
    create_pool_mock = AsyncMock(return_value=fake_pool)
    monkeypatch.setattr(db.asyncpg, "create_pool", create_pool_mock)

    first = await db.get_pool()
    second = await db.get_pool()

    assert first is fake_pool
    assert second is fake_pool
    create_pool_mock.assert_awaited_once_with(
        dsn="postgres://user:pass@localhost:5432/testdb",
        min_size=1,
        max_size=10,
        command_timeout=30,
    )


@pytest.mark.asyncio
async def test_close_pool_closes_and_resets(monkeypatch):
    fake_pool = AsyncMock()
    monkeypatch.setattr(db, "_pool", fake_pool)

    await db.close_pool()

    fake_pool.close.assert_awaited_once()
    assert db._pool is None


@pytest.mark.asyncio
async def test_close_pool_noop_when_pool_not_initialized(monkeypatch):
    monkeypatch.setattr(db, "_pool", None)

    await db.close_pool()

    assert db._pool is None
