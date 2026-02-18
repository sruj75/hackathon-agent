"""Shared pytest fixtures for testing."""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock

import cron_service

# Set test environment
os.environ['ENVIRONMENT'] = 'test'
os.environ.setdefault('COMPOSIO_CACHE_DIR', '/tmp/composio-cache')
os.makedirs(os.environ['COMPOSIO_CACHE_DIR'], exist_ok=True)


@pytest.fixture
def mock_cron_api(monkeypatch):
    """Backward-compatible scheduler fixture for cron regression tests."""
    schedule_mock = AsyncMock(return_value=12345)
    unschedule_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(cron_service.event_repo, "schedule_event_job", schedule_mock)
    monkeypatch.setattr(cron_service.event_repo, "unschedule_event_job", unschedule_mock)
    return MagicMock(schedule=schedule_mock, unschedule=unschedule_mock)


@pytest.fixture
def test_user():
    """Minimal user fixture for regression suites that only need user_id."""
    return MagicMock(user_id="test_user")
