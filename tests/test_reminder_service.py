"""Unit tests for reminder_service.py."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock

import pytest
import time_machine
import reminder_service


@pytest.mark.asyncio
async def test_schedule_calendar_reminder_creates_t_minus_five(monkeypatch):
    list_pending_mock = AsyncMock(return_value=[])
    create_event_mock = AsyncMock(return_value={"id": "reminder_1"})
    create_cron_mock = AsyncMock(return_value=12345)
    update_cron_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(
        reminder_service.event_repo,
        "list_pending_by_calendar_event",
        list_pending_mock,
    )
    monkeypatch.setattr(reminder_service.event_repo, "create_event", create_event_mock)
    monkeypatch.setattr(reminder_service.cron_service, "create_one_time_job", create_cron_mock)
    monkeypatch.setattr(reminder_service.event_repo, "update_cron_job_id", update_cron_mock)

    result = await reminder_service.schedule_calendar_reminder(
        user_id="user_test",
        calendar_event_id="cal_1",
        event_title="Deep Work",
        event_start_time="2099-01-01T10:00:00+00:00",
        timezone_name="UTC",
        lead_minutes=5,
    )

    assert result["status"] == "scheduled"
    assert result["immediate"] is False
    assert create_event_mock.await_count == 1
    scheduled_time = create_event_mock.await_args.kwargs["scheduled_time"]
    assert scheduled_time.isoformat().startswith("2099-01-01T09:55:00")


@pytest.mark.asyncio
async def test_schedule_calendar_reminder_uses_immediate_window(monkeypatch):
    now_utc = datetime.now(ZoneInfo("UTC"))
    start_soon = now_utc + timedelta(minutes=2)

    monkeypatch.setattr(
        reminder_service.event_repo,
        "list_pending_by_calendar_event",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        reminder_service.event_repo,
        "create_event",
        AsyncMock(return_value={"id": "reminder_2"}),
    )
    create_cron_mock = AsyncMock(return_value=777)
    monkeypatch.setattr(reminder_service.cron_service, "create_one_time_job", create_cron_mock)
    monkeypatch.setattr(
        reminder_service.event_repo,
        "update_cron_job_id",
        AsyncMock(return_value=True),
    )

    result = await reminder_service.schedule_calendar_reminder(
        user_id="user_test",
        calendar_event_id="cal_2",
        event_title="Soon Event",
        event_start_time=start_soon.isoformat(),
        timezone_name="UTC",
        lead_minutes=5,
    )

    assert result["immediate"] is True
    target_datetime = create_cron_mock.await_args.kwargs["target_datetime"]
    assert target_datetime > now_utc + timedelta(seconds=5)
    assert target_datetime < now_utc + timedelta(seconds=30)


@pytest.mark.asyncio
@time_machine.travel("2024-01-15T12:00:00+00:00", tick=False)
async def test_schedule_calendar_reminder_overwrites_existing(monkeypatch):
    existing = {
        "id": "reminder_existing",
        "cron_job_id": 100,
        "scheduled_time": datetime(2099, 1, 1, 9, 55, tzinfo=ZoneInfo("UTC")),
    }
    duplicate = {
        "id": "reminder_duplicate",
        "cron_job_id": 200,
        "scheduled_time": datetime(2099, 1, 1, 9, 54, tzinfo=ZoneInfo("UTC")),
    }

    monkeypatch.setattr(
        reminder_service.event_repo,
        "list_pending_by_calendar_event",
        AsyncMock(return_value=[existing, duplicate]),
    )
    delete_cron_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(reminder_service.cron_service, "delete_job", delete_cron_mock)
    monkeypatch.setattr(reminder_service.event_repo, "mark_cancelled", AsyncMock(return_value=1))
    monkeypatch.setattr(
        reminder_service.cron_service,
        "create_one_time_job",
        AsyncMock(return_value=300),
    )
    update_event_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(reminder_service.event_repo, "update_event", update_event_mock)

    result = await reminder_service.schedule_calendar_reminder(
        user_id="user_test",
        calendar_event_id="cal_3",
        event_title="Rescheduled Event",
        event_start_time="2099-01-01T11:00:00+00:00",
        timezone_name="UTC",
        lead_minutes=5,
    )

    assert result["event_id"] == "reminder_existing"
    assert delete_cron_mock.await_count == 2
    update_event_mock.assert_awaited_once()
    assert update_event_mock.await_args.args[0] == "reminder_existing"
    assert update_event_mock.await_args.kwargs["cron_job_id"] == 300


@pytest.mark.asyncio
async def test_cancel_calendar_reminders_marks_rows_cancelled(monkeypatch):
    pending = [
        {"id": "r1", "cron_job_id": 111},
        {"id": "r2", "cron_job_id": 222},
    ]
    monkeypatch.setattr(
        reminder_service.event_repo,
        "list_pending_by_calendar_event",
        AsyncMock(return_value=pending),
    )
    delete_job_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(reminder_service.cron_service, "delete_job", delete_job_mock)
    mark_cancelled_mock = AsyncMock(return_value=2)
    monkeypatch.setattr(reminder_service.event_repo, "mark_cancelled", mark_cancelled_mock)

    result = await reminder_service.cancel_calendar_reminders(
        user_id="user_test",
        calendar_event_id="cal_4",
        reason="calendar_event_deleted",
    )

    assert result == {"status": "cancelled", "cancelled": 2}
    assert delete_job_mock.await_count == 2
    mark_cancelled_mock.assert_awaited_once_with(["r1", "r2"], reason="calendar_event_deleted")
