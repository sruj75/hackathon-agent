"""Unit tests for ADKSessionManager with current Firestore-era repository contracts."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun import freeze_time

from session_manager import ADKSessionManager


def _build_service(get_session_return=None, create_session_return=None):
    service = MagicMock()
    service.get_session = AsyncMock(return_value=get_session_return)
    service.create_session = AsyncMock(return_value=create_session_return)
    return service


def _build_session(session_id="session_test", user_id="user_test", state=None):
    session = MagicMock()
    session.id = session_id
    session.user_id = user_id
    session.state = state or {}
    session.events = []
    return session


def test_get_daily_session_id_is_deterministic():
    with freeze_time("2026-02-04"):
        assert ADKSessionManager.get_daily_session_id("user_test") == "session_user_test_2026-02-04"


@pytest.mark.asyncio
async def test_get_or_create_session_returns_memory_session():
    existing = _build_session("session_existing")
    service = _build_service(get_session_return=existing)
    manager = ADKSessionManager(service=service)

    result = await manager.get_or_create_session(
        app_name="test_app",
        user_id="user_test",
        session_id="session_existing",
    )

    assert result is existing
    service.get_session.assert_awaited_once()
    service.create_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_or_create_session_restores_from_db(monkeypatch):
    restored = _build_session("session_restored", state={"history": ["x"]})
    service = _build_service(get_session_return=None, create_session_return=restored)
    manager = ADKSessionManager(service=service)

    get_db_session_mock = AsyncMock(
        return_value={"session_id": "session_restored", "state": {"history": ["x"]}}
    )
    monkeypatch.setattr("session_manager.session_repo.get_session", get_db_session_mock)

    result = await manager.get_or_create_session(
        app_name="test_app",
        user_id="user_test",
        session_id="session_restored",
    )

    assert result is restored
    service.create_session.assert_awaited_once_with(
        app_name="test_app",
        user_id="user_test",
        session_id="session_restored",
        state={"history": ["x"]},
    )


@pytest.mark.asyncio
async def test_get_or_create_session_creates_new_when_not_found(monkeypatch):
    created = _build_session("session_new")
    service = _build_service(get_session_return=None, create_session_return=created)
    manager = ADKSessionManager(service=service)

    monkeypatch.setattr("session_manager.session_repo.get_session", AsyncMock(return_value=None))

    result = await manager.get_or_create_session(
        app_name="test_app",
        user_id="user_test",
        session_id="session_new",
    )

    assert result is created
    service.create_session.assert_awaited_once_with(
        app_name="test_app",
        user_id="user_test",
        session_id="session_new",
    )


@pytest.mark.asyncio
async def test_save_agent_session_to_db_uses_upsert(monkeypatch):
    manager = ADKSessionManager(service=_build_service())
    upsert_mock = AsyncMock(return_value={})
    monkeypatch.setattr("session_manager.session_repo.upsert_session", upsert_mock)

    ok = await manager.save_agent_session_to_db(
        session_id="session_1",
        state={"user_id": "user_test", "date": "2026-02-04", "x": 1},
    )

    assert ok is True
    upsert_mock.assert_awaited_once_with(
        session_id="session_1",
        user_id="user_test",
        date="2026-02-04",
        state={"user_id": "user_test", "date": "2026-02-04", "x": 1},
    )


@pytest.mark.asyncio
async def test_save_agent_session_to_db_falls_back_without_user_id(monkeypatch):
    manager = ADKSessionManager(service=_build_service())
    save_session_mock = AsyncMock(return_value={})

    monkeypatch.setattr("session_manager.session_repo.save_session", save_session_mock)
    monkeypatch.setattr("session_manager.session_repo.upsert_session", AsyncMock())

    ok = await manager.save_agent_session_to_db(
        session_id="session_2",
        state={"date": "2026-02-04", "x": 2},
    )

    assert ok is True
    save_session_mock.assert_awaited_once_with(
        "session_2",
        {"date": "2026-02-04", "x": 2},
    )


@pytest.mark.asyncio
async def test_restore_session_from_db_returns_none_when_missing(monkeypatch):
    service = _build_service()
    manager = ADKSessionManager(service=service)

    monkeypatch.setattr("session_manager.session_repo.get_session", AsyncMock(return_value=None))

    result = await manager.restore_session_from_db(
        app_name="test_app",
        session_id="session_missing",
        user_id="user_test",
    )

    assert result is None


@pytest.mark.asyncio
async def test_restore_session_from_db_hydrates_state(monkeypatch):
    restored = _build_session("session_restore", state={"conversation": [1]})
    service = _build_service(create_session_return=restored)
    manager = ADKSessionManager(service=service)

    monkeypatch.setattr(
        "session_manager.session_repo.get_session",
        AsyncMock(return_value={"state": {"conversation": [1]}}),
    )

    result = await manager.restore_session_from_db(
        app_name="test_app",
        session_id="session_restore",
        user_id="user_test",
    )

    assert result is restored
    service.create_session.assert_awaited_once_with(
        app_name="test_app",
        user_id="user_test",
        session_id="session_restore",
        state={"conversation": [1]},
    )
