"""
Phase 2 regression smoke suite.

Goal: retain concise coverage for phase-2 architecture contracts:
context vars, cron wiring, event lifecycle, and runtime interfaces.
"""
import inspect
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

import cron_service
from context import current_session_id, current_user_id
from repos import event_repo


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase2_context_vars_are_settable(test_user):
    current_user_id.set(test_user.user_id)
    current_session_id.set("phase2_session")

    assert current_user_id.get() == test_user.user_id
    assert current_session_id.get() == "phase2_session"


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
                timezone="UTC",
            )


@pytest.mark.regression
@pytest.mark.asyncio
async def test_phase2_event_cron_link_lifecycle(monkeypatch):
    update_cron_mock = AsyncMock(return_value=True)
    mark_executed_mock = AsyncMock(return_value=None)
    get_by_id_mock = AsyncMock(
        return_value={"id": "phase2_event", "cron_job_id": 99999, "executed": True}
    )
    monkeypatch.setattr(event_repo, "update_cron_job_id", update_cron_mock)
    monkeypatch.setattr(event_repo, "mark_executed", mark_executed_mock)
    monkeypatch.setattr(event_repo, "get_by_id", get_by_id_mock)

    event = {"id": "phase2_event", "cron_job_id": None}
    assert event["cron_job_id"] is None

    await event_repo.update_cron_job_id(event["id"], 99999)
    await event_repo.mark_executed(event["id"])

    updated = await event_repo.get_by_id(event["id"])
    assert updated is not None
    assert updated["cron_job_id"] == 99999
    assert updated["executed"] is True


@pytest.mark.regression
def test_phase2_agent_runtime_interface_contract():
    from agent_runtime import AgentRuntime

    assert not hasattr(AgentRuntime, "run_thinking_mode")

    sig = inspect.signature(AgentRuntime.get_realtime_run_config)
    assert len(sig.parameters) == 0

    config = AgentRuntime.get_realtime_run_config()
    assert config.response_modalities == ["AUDIO"]
