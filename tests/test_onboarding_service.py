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
@time_machine.travel("2026-02-26T10:30:00+00:00", tick=False)
async def test_save_onboarding_progress_for_user_merges_playbook_and_keeps_pending(monkeypatch):
    existing_profile = {
        "user_id": "user_1",
        "onboarding_status": "pending",
        "onboarding_completed_at": None,
        "wake_time": "07:00",
        "bedtime": "22:00",
        "timezone": "UTC",
        "health_anchors": [],
        "playbook": {
            "schema_version": "1.0",
            "created_at": "2026-02-20T00:00:00+00:00",
            "summary": "Old summary",
            "struggles": ["overwhelm"],
        },
    }
    updated_profile = {
        **existing_profile,
        "wake_time": "07:30",
        "bedtime": "22:30",
        "timezone": "America/New_York",
        "playbook": {
            "schema_version": "1.0",
            "created_at": "2026-02-20T00:00:00+00:00",
            "updated_at": "2026-02-26T10:30:00+00:00",
            "summary": "Needs shorter focus blocks",
            "struggles": ["procrastination"],
            "goals": ["consistent morning start"],
            "communication_style": "direct",
        },
    }

    get_profile_mock = AsyncMock(side_effect=[existing_profile, updated_profile])
    update_profile_mock = AsyncMock(return_value=updated_profile)
    monkeypatch.setattr(onboarding_service.user_repo, "get_profile", get_profile_mock)
    monkeypatch.setattr(onboarding_service.user_repo, "update_profile", update_profile_mock)

    result = await onboarding_service.save_onboarding_progress_for_user(
        "user_1",
        wake_time="07:30",
        bedtime="22:30",
        timezone_name="America/New_York",
        summary="Needs shorter focus blocks",
        struggles=["procrastination", " "],
        goals=["consistent morning start"],
        communication_style="direct",
    )

    update_kwargs = update_profile_mock.await_args.kwargs
    assert update_kwargs["wake_time"] == "07:30"
    assert update_kwargs["bedtime"] == "22:30"
    assert update_kwargs["timezone"] == "America/New_York"
    assert update_kwargs["onboarding_status"] == onboarding_service.ONBOARDING_STATUS_PENDING
    assert update_kwargs["onboarding_completed_at"] is None
    assert update_kwargs["playbook"]["created_at"] == "2026-02-20T00:00:00+00:00"
    assert update_kwargs["playbook"]["updated_at"] == "2026-02-26T10:30:00+00:00"
    assert update_kwargs["playbook"]["summary"] == "Needs shorter focus blocks"
    assert update_kwargs["playbook"]["struggles"] == ["procrastination"]
    assert update_kwargs["playbook"]["goals"] == ["consistent morning start"]
    assert update_kwargs["playbook"]["communication_style"] == "direct"
    assert result["status"] == "ok"
    assert result["context"]["onboarding_status"] == onboarding_service.ONBOARDING_STATUS_PENDING


@pytest.mark.asyncio
async def test_save_onboarding_progress_for_user_validates_inputs(monkeypatch):
    monkeypatch.setattr(onboarding_service.user_repo, "get_profile", AsyncMock(return_value={}))
    monkeypatch.setattr(onboarding_service.user_repo, "update_profile", AsyncMock())

    with pytest.raises(ValueError, match="wake_time must be HH:MM"):
        await onboarding_service.save_onboarding_progress_for_user(
            "user_1",
            wake_time="7:30",
        )

    with pytest.raises(ValueError, match="bedtime must be HH:MM"):
        await onboarding_service.save_onboarding_progress_for_user(
            "user_1",
            bedtime="bad",
        )

    with pytest.raises(ValueError, match="timezone must be a valid IANA timezone"):
        await onboarding_service.save_onboarding_progress_for_user(
            "user_1",
            timezone_name="Not/A_Real_Timezone",
        )


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
