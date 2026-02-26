"""Unit tests for onboarding_service.py."""

from unittest.mock import AsyncMock

import pytest
import time_machine

import onboarding_service


pytestmark = pytest.mark.unit


def test_validate_playbook_for_completion_reports_all_missing_fields():
    errors = onboarding_service.validate_playbook_for_completion({})
    assert "playbook.summary is required" in errors
    assert "playbook.struggles must contain at least one item" in errors
    assert "playbook.goals must contain at least one item" in errors
    assert "playbook.communication_style is required" in errors


def test_normalize_timezone_accepts_valid_and_rejects_invalid():
    assert onboarding_service._normalize_timezone("UTC") == "UTC"
    assert onboarding_service._normalize_timezone("Not/A_Real_Timezone") is None
    assert onboarding_service._normalize_timezone(None) is None


@pytest.mark.asyncio
async def test_get_onboarding_context_for_user_returns_not_found(monkeypatch):
    monkeypatch.setattr(onboarding_service.user_repo, "get_profile", AsyncMock(return_value=None))

    result = await onboarding_service.get_onboarding_context_for_user("user_missing")

    assert result == {
        "status": "not_found",
        "user_id": "user_missing",
        "context": None,
    }


@pytest.mark.asyncio
async def test_get_onboarding_context_for_user_normalizes_status_and_defaults(monkeypatch):
    profile = {
        "user_id": "user_1",
        "onboarding_status": "IN_PROGRESS",
        "onboarding_completed_at": None,
        "playbook": None,
        "wake_time": "07:30",
        "bedtime": "22:00",
        "timezone": "UTC",
        "health_anchors": None,
    }
    monkeypatch.setattr(onboarding_service.user_repo, "get_profile", AsyncMock(return_value=profile))

    result = await onboarding_service.get_onboarding_context_for_user("user_1")

    assert result["status"] == "ok"
    context = result["context"]
    assert context["onboarding_status"] == onboarding_service.ONBOARDING_STATUS_PENDING
    assert context["playbook"] == {}
    assert context["preferences"]["health_anchors"] == []


@pytest.mark.asyncio
async def test_complete_onboarding_for_user_requires_valid_inputs(monkeypatch):
    monkeypatch.setattr(onboarding_service.user_repo, "get_profile", AsyncMock(return_value=None))

    with pytest.raises(ValueError, match="wake_time must be HH:MM"):
        await onboarding_service.complete_onboarding_for_user(
            "user_1",
            wake_time="7:30",
            bedtime="22:00",
            timezone_name="UTC",
            playbook=None,
        )

    with pytest.raises(ValueError, match="bedtime must be HH:MM"):
        await onboarding_service.complete_onboarding_for_user(
            "user_1",
            wake_time="07:30",
            bedtime="bad",
            timezone_name="UTC",
            playbook=None,
        )


@pytest.mark.asyncio
async def test_complete_onboarding_for_user_requires_timezone(monkeypatch):
    monkeypatch.setattr(
        onboarding_service.user_repo,
        "get_profile",
        AsyncMock(return_value={"timezone": "Invalid/Timezone"}),
    )

    with pytest.raises(ValueError, match="timezone is required"):
        await onboarding_service.complete_onboarding_for_user(
            "user_1",
            wake_time="07:30",
            bedtime="22:00",
            timezone_name=None,
            playbook={
                "summary": "Profile summary",
                "struggles": ["procrastination"],
                "goals": ["start earlier"],
                "communication_style": "direct",
            },
        )


@pytest.mark.asyncio
@time_machine.travel("2026-02-26T10:30:00+00:00", tick=False)
async def test_complete_onboarding_for_user_updates_profile_with_normalized_playbook(
    monkeypatch,
):
    monkeypatch.setattr(
        onboarding_service.user_repo,
        "get_profile",
        AsyncMock(return_value={"timezone": "America/New_York"}),
    )
    update_profile_mock = AsyncMock(return_value={"user_id": "user_1"})
    monkeypatch.setattr(onboarding_service.user_repo, "update_profile", update_profile_mock)

    updated_profile, completion_time = await onboarding_service.complete_onboarding_for_user(
        "user_1",
        wake_time="07:30",
        bedtime="22:15",
        timezone_name=None,
        playbook={
            "summary": "Best with short sprints",
            "struggles": ["context switching", "  "],
            "goals": ["finish one deep-work block"],
            "communication_style": "concise",
        },
        health_anchors=["sleep", "exercise"],
    )

    assert updated_profile == {"user_id": "user_1"}
    assert completion_time.isoformat() == "2026-02-26T10:30:00+00:00"
    update_profile_mock.assert_awaited_once()
    update_kwargs = update_profile_mock.await_args.kwargs
    assert update_kwargs["wake_time"] == "07:30"
    assert update_kwargs["bedtime"] == "22:15"
    assert update_kwargs["timezone"] == "America/New_York"
    assert update_kwargs["onboarding_status"] == onboarding_service.ONBOARDING_STATUS_COMPLETED
    assert update_kwargs["health_anchors"] == ["sleep", "exercise"]
    assert update_kwargs["playbook"]["profile"]["timezone"] == "America/New_York"
    assert update_kwargs["playbook"]["struggles"] == ["context switching"]
    assert update_kwargs["playbook"]["goals"] == ["finish one deep-work block"]


@pytest.mark.asyncio
async def test_complete_onboarding_for_user_rejects_incomplete_playbook(monkeypatch):
    monkeypatch.setattr(
        onboarding_service.user_repo,
        "get_profile",
        AsyncMock(return_value={"timezone": "UTC"}),
    )
    monkeypatch.setattr(onboarding_service.user_repo, "update_profile", AsyncMock())

    with pytest.raises(ValueError, match="playbook.summary is required"):
        await onboarding_service.complete_onboarding_for_user(
            "user_1",
            wake_time="07:30",
            bedtime="22:00",
            timezone_name="UTC",
            playbook={"summary": "", "struggles": [], "goals": []},
        )
