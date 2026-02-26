"""Unit tests for pg_cron-backed cron_service."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

import cron_service


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_create_one_time_job_delegates_to_event_repo(monkeypatch):
    schedule_mock = AsyncMock(return_value=12345)
    monkeypatch.setattr(cron_service.event_repo, "schedule_event_job", schedule_mock)

    job_id = await cron_service.create_one_time_job(
        target_datetime=datetime(2026, 2, 5, 8, 0, 0),
        event_id="event_1",
        timezone="UTC",
    )

    assert job_id == 12345
    assert schedule_mock.await_count == 1
    kwargs = schedule_mock.await_args.kwargs
    assert kwargs["event_id"] == "event_1"
    assert kwargs["timezone_name"] == "UTC"
    assert kwargs["run_at"].tzinfo == timezone.utc


@pytest.mark.asyncio
async def test_create_one_time_job_missing_timezone():
    with pytest.raises(ValueError, match="missing_timezone"):
        await cron_service.create_one_time_job(
            target_datetime=datetime.now(timezone.utc),
            event_id="event_1",
            timezone="",
        )


@pytest.mark.asyncio
async def test_create_one_time_job_missing_event_id():
    with pytest.raises(ValueError, match="missing_event_id"):
        await cron_service.create_one_time_job(
            target_datetime=datetime.now(timezone.utc),
            event_id="",
            timezone="UTC",
        )


@pytest.mark.asyncio
async def test_delete_job_calls_unschedule(monkeypatch):
    unschedule_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(cron_service.event_repo, "unschedule_event_job", unschedule_mock)

    deleted = await cron_service.delete_job(4242)

    assert deleted is True
    unschedule_mock.assert_awaited_once_with(4242)


@pytest.mark.asyncio
async def test_delete_job_missing_id_returns_false():
    deleted = await cron_service.delete_job(None)
    assert deleted is False


@pytest.mark.asyncio
async def test_delete_job_failure_returns_false(monkeypatch):
    unschedule_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(cron_service.event_repo, "unschedule_event_job", unschedule_mock)

    deleted = await cron_service.delete_job(4242)

    assert deleted is False
