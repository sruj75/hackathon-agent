from unittest.mock import AsyncMock

import pytest

import main
from context import current_user_id, current_user_timezone
from voice_agent import onboarding_tools


@pytest.mark.asyncio
async def test_get_onboarding_context_requires_user_context():
    result = await onboarding_tools.get_onboarding_context()
    assert result["status"] == "error"
    assert result["error"] == "missing_user_context"


@pytest.mark.asyncio
async def test_get_onboarding_context_success(monkeypatch):
    token = current_user_id.set("user_test")
    try:
        get_context_mock = AsyncMock(
            return_value={
                "status": "ok",
                "user_id": "user_test",
                "context": {"onboarding_status": "pending"},
            }
        )
        monkeypatch.setattr(
            onboarding_tools,
            "get_onboarding_context_for_user",
            get_context_mock,
        )

        result = await onboarding_tools.get_onboarding_context()

        assert result["status"] == "ok"
        get_context_mock.assert_awaited_once_with("user_test")
    finally:
        current_user_id.reset(token)


@pytest.mark.asyncio
async def test_complete_onboarding_calls_shared_workflow(monkeypatch):
    user_token = current_user_id.set("user_test")
    tz_token = current_user_timezone.set("America/New_York")
    try:
        workflow_mock = AsyncMock(
            return_value={
                "status": "ok",
                "onboarding_status": "completed",
                "onboarding_completed_at": "2026-02-25T00:00:00+00:00",
                "route_hint": "assistant",
                "scheduler": {"resynced": True, "error": None},
            }
        )
        monkeypatch.setattr(main, "_complete_onboarding_workflow", workflow_mock)

        result = await onboarding_tools.complete_onboarding(
            wake_time="07:30",
            bedtime="22:15",
            playbook_json='{"summary":"test"}',
        )

        assert result["status"] == "ok"
        assert result["onboarding_status"] == "completed"
        workflow_mock.assert_awaited_once_with(
            user_id="user_test",
            wake_time="07:30",
            bedtime="22:15",
            timezone_name="America/New_York",
            playbook={"summary": "test"},
            health_anchors=None,
        )
    finally:
        current_user_id.reset(user_token)
        current_user_timezone.reset(tz_token)


@pytest.mark.asyncio
async def test_complete_onboarding_rejects_invalid_playbook_json():
    user_token = current_user_id.set("user_test")
    tz_token = current_user_timezone.set("America/New_York")
    try:
        result = await onboarding_tools.complete_onboarding(
            wake_time="07:30",
            bedtime="22:15",
            playbook_json="{invalid-json",
        )
        assert result["status"] == "error"
        assert result["error"] == "playbook_json must be valid JSON"
    finally:
        current_user_id.reset(user_token)
        current_user_timezone.reset(tz_token)
