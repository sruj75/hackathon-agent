"""
Phase 1 regression smoke suite.

Goal: keep a small, stable safety net for foundational phase-1 behavior.
Detailed CRUD behavior is covered in unit suites.
"""
from datetime import datetime, timedelta

import pytest
from freezegun import freeze_time

from repos import event_repo, session_repo, user_repo
from session_manager import ADKSessionManager


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase1_user_profile_and_push_token_roundtrip(test_db):
    user = await user_repo.create_profile(
        db=test_db,
        user_id="phase1_user",
        wake_time="07:30",
        bedtime="22:30",
        timezone="America/New_York",
        health_anchors=["sleep", "exercise"],
    )
    assert user.user_id == "phase1_user"

    await user_repo.save_push_token(
        db=test_db,
        user_id=user.user_id,
        token="ExponentPushToken[phase1_token]",
    )
    assert await user_repo.get_push_token(test_db, user.user_id) == "ExponentPushToken[phase1_token]"


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase1_session_save_and_lookup_by_date(test_db, test_user):
    await session_repo.upsert_session(
        db=test_db,
        session_id="phase1_session",
        user_id=test_user.user_id,
        date="2026-02-07",
        state={"conversation": [{"role": "user", "message": "hello"}]},
    )

    session = await session_repo.get_today_session(
        db=test_db, user_id=test_user.user_id, date="2026-02-07"
    )
    assert session is not None
    assert session.session_id == "phase1_session"


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase1_event_lifecycle_with_execution_mark(test_db, test_user):
    event = await event_repo.create_event(
        db=test_db,
        user_id=test_user.user_id,
        scheduled_time=datetime.utcnow() + timedelta(minutes=15),
        event_type="checkin",
        payload={"reason": "phase1_smoke"},
    )
    assert event.executed is False

    await event_repo.mark_executed(test_db, event.id)
    updated = await event_repo.get_by_id(test_db, event.id)
    assert updated is not None
    assert updated.executed is True


@pytest.mark.regression
def test_phase1_daily_session_id_is_deterministic():
    with freeze_time("2026-02-07"):
        assert (
            ADKSessionManager.get_daily_session_id("phase1_user")
            == "session_phase1_user_2026-02-07"
        )

