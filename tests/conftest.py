"""Shared pytest fixtures for testing."""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Set test environment
os.environ['ENVIRONMENT'] = 'test'
os.environ.setdefault('COMPOSIO_CACHE_DIR', '/tmp/composio-cache')
os.makedirs(os.environ['COMPOSIO_CACHE_DIR'], exist_ok=True)


@pytest.fixture
def mock_httpx_client():
    """Mock cron_service httpx AsyncClient context manager."""
    with patch("cron_service.httpx.AsyncClient") as mock_client_class:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value={"jobId": 12345, "status": "OK"})
        mock_response.raise_for_status = MagicMock(return_value=None)

        mock_client_instance = MagicMock()
        mock_client_instance.put = AsyncMock(return_value=mock_response)
        mock_client_instance.delete = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)
        mock_client_class.return_value = mock_client_instance
        yield mock_client_instance


@pytest.fixture
def mock_cron_api(mock_httpx_client):
    """Alias fixture expected by cron regression tests."""
    return mock_httpx_client


@pytest.fixture
def test_user():
    """Minimal user fixture for regression suites that only need user_id."""
    return MagicMock(user_id="test_user")
