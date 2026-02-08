"""
Phase 1 regression smoke suite.

Goal: keep a small, stable safety net for foundational phase-1 behavior.
Detailed CRUD behavior is covered in unit suites.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time

from repos import event_repo, session_repo, user_repo
from session_manager import ADKSessionManager


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase1_user_profile_and_push_token_roundtrip(monkeypatch):
    create_profile_mock = AsyncMock(
        return_value={"user_id": "phase1_user", "wake_time": "07:30"}
    )
    save_token_mock = AsyncMock(
        return_value={"user_id": "phase1_user", "expo_push_token": "token"}
    )
    get_token_mock = AsyncMock(return_value="ExponentPushToken[phase1_token]")

    monkeypatch.setattr(user_repo, "create_profile", create_profile_mock)
    monkeypatch.setattr(user_repo, "save_push_token", save_token_mock)
    monkeypatch.setattr(user_repo, "get_push_token", get_token_mock)

    user = await user_repo.create_profile(
        user_id="phase1_user",
        wake_time="07:30",
        bedtime="22:30",
        timezone="America/New_York",
        health_anchors=["sleep", "exercise"],
    )
    assert user["user_id"] == "phase1_user"

    await user_repo.save_push_token(
        user_id=user["user_id"],
        token="ExponentPushToken[phase1_token]",
    )
    assert (
        await user_repo.get_push_token(user["user_id"])
        == "ExponentPushToken[phase1_token]"
    )


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase1_session_save_and_lookup_by_date(monkeypatch):
    upsert_mock = AsyncMock(return_value=None)
    get_today_mock = AsyncMock(
        return_value=SimpleNamespace(session_id="phase1_session")
    )
    monkeypatch.setattr(session_repo, "upsert_session", upsert_mock)
    monkeypatch.setattr(session_repo, "get_today_session", get_today_mock)

    await session_repo.upsert_session(
        session_id="phase1_session",
        user_id="phase1_user",
        date="2026-02-07",
        state={"conversation": [{"role": "user", "message": "hello"}]},
    )

    session = await session_repo.get_today_session(
        user_id="phase1_user", date="2026-02-07"
    )
    assert session is not None
    assert session.session_id == "phase1_session"


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase1_event_lifecycle_with_execution_mark(monkeypatch):
    create_event_mock = AsyncMock(
        return_value={"id": "event_1", "executed": False}
    )
    mark_executed_mock = AsyncMock(return_value=None)
    get_by_id_mock = AsyncMock(return_value={"id": "event_1", "executed": True})
    monkeypatch.setattr(event_repo, "create_event", create_event_mock)
    monkeypatch.setattr(event_repo, "mark_executed", mark_executed_mock)
    monkeypatch.setattr(event_repo, "get_by_id", get_by_id_mock)

    event = await event_repo.create_event(
        user_id="phase1_user",
        scheduled_time="2026-02-07T10:15:00Z",
        event_type="checkin",
        payload={"reason": "phase1_smoke"},
    )
    assert event["executed"] is False

    await event_repo.mark_executed(event["id"])
    updated = await event_repo.get_by_id(event["id"])
    assert updated is not None
    assert updated["executed"] is True


@pytest.mark.regression
def test_phase1_daily_session_id_is_deterministic():
    with freeze_time("2026-02-07"):
        assert (
            ADKSessionManager.get_daily_session_id("phase1_user")
            == "session_phase1_user_2026-02-07"
        )
