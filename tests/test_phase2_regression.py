"""
Phase 2 regression smoke suite.

Goal: retain concise coverage for phase-2 architecture contracts:
context vars, cron wiring, event lifecycle, and runtime interfaces.
"""
import inspect
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

import cron_service
from context import current_db, current_session_id, current_user_id
from repos import event_repo


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase2_context_vars_are_settable(test_db, test_user):
    current_user_id.set(test_user.user_id)
    current_session_id.set("phase2_session")
    current_db.set(test_db)

    assert current_user_id.get() == test_user.user_id
    assert current_session_id.get() == "phase2_session"
    assert current_db.get() == test_db


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase2_cron_create_and_delete(mock_cron_api):
    mock_cron_api.put.return_value.json.return_value = {"jobId": 424242}
    mock_cron_api.delete.return_value.status_code = 200

    with patch("cron_service.CRONJOB_API_KEY", "test_key"):
        job_id = await cron_service.create_one_time_job(
            target_datetime=datetime(2026, 2, 7, 9, 0, 0),
            event_id="phase2_event",
            timezone="UTC",
        )
        deleted = await cron_service.delete_job(job_id)

    assert job_id == 424242
    assert deleted is True


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase2_cron_missing_api_key_errors_cleanly():
    with patch("cron_service.CRONJOB_API_KEY", None):
        with pytest.raises(ValueError, match="CRONJOB_ORG_API_KEY"):
            await cron_service.create_one_time_job(
                target_datetime=datetime.utcnow(),
                event_id="phase2_missing_key",
            )


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase2_event_cron_link_lifecycle(test_db, test_user):
    event = await event_repo.create_event(
        db=test_db,
        user_id=test_user.user_id,
        scheduled_time=datetime.utcnow() + timedelta(minutes=20),
        event_type="checkin",
        payload={"reason": "phase2_smoke"},
    )
    assert event.cron_job_id is None

    await event_repo.update_cron_job_id(test_db, event.id, 99999)
    await event_repo.mark_executed(test_db, event.id)

    updated = await event_repo.get_by_id(test_db, event.id)
    assert updated is not None
    assert updated.cron_job_id == 99999
    assert updated.executed is True


@pytest.mark.regression
def test_phase2_agent_runtime_interface_contract():
    from agent_runtime import AgentRuntime

    sig = inspect.signature(AgentRuntime.run_thinking_mode)
    params = list(sig.parameters.keys())
    assert "user_id" in params
    assert "trigger_context" in params
    assert "session_manager" in params
    assert "db" in params

    config = AgentRuntime.get_conversation_mode_config()
    assert config.response_modalities == ["AUDIO"]

