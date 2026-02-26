"""
Unit tests for notification_service.py

Tests:
- Mock Expo API
- Verify token retrieval from DB
- Verify error handling (DeviceNotRegistered)
- Verify payload format
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from notification_service import send_push_notification, delete_push_token


pytestmark = pytest.mark.unit


@pytest.fixture
def mock_user_repo():
    """Mock user repository."""
    with patch('notification_service.user_repo') as mock:
        yield mock


class TestNotificationService:
    """Test suite for notification service."""

    @pytest.mark.asyncio
    async def test_send_notification_success(self, mock_user_repo):
        """Test successful notification send."""
        # Setup
        mock_user_repo.get_push_token = AsyncMock(return_value="ExponentPushToken[test123]")
        
        with patch('notification_service.httpx.AsyncClient') as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "data": [{"status": "ok"}]
            }
            
            mock_client_instance = MagicMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client_instance
            
            # Execute
            result = await send_push_notification(
                user_id="user_default",
                title="Test",
                body="Test message",
                data={"session_id": "session_123", "type": "test"}
            )
            
            # Verify
            assert result is True
            mock_user_repo.get_push_token.assert_called_once_with("user_default")

    @pytest.mark.asyncio
    async def test_send_notification_no_token(self, mock_user_repo):
        """Test notification send when user has no token."""
        # Setup
        mock_user_repo.get_push_token = AsyncMock(return_value=None)
        
        # Execute
        result = await send_push_notification(
            user_id="user_default",
            title="Test",
            body="Test message"
        )
        
        # Verify
        assert result is False
        mock_user_repo.get_push_token.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_notification_device_not_registered(self, mock_user_repo):
        """Test handling of DeviceNotRegistered error."""
        # Setup
        mock_user_repo.get_push_token = AsyncMock(return_value="ExponentPushToken[test123]")
        
        with patch('notification_service.httpx.AsyncClient') as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "data": [{
                    "status": "error",
                    "details": {"error": "DeviceNotRegistered"}
                }]
            }
            
            mock_client_instance = MagicMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client_instance
            
            with patch('notification_service.delete_push_token') as mock_delete:
                mock_delete.return_value = AsyncMock()
                
                # Execute
                result = await send_push_notification(
                    user_id="user_default",
                    title="Test",
                    body="Test message"
                )
                
                # Verify
                assert result is False
                mock_delete.assert_called_once_with("user_default")

    @pytest.mark.asyncio
    async def test_send_notification_expo_api_error(self, mock_user_repo):
        """Test handling of Expo API errors."""
        # Setup
        mock_user_repo.get_push_token = AsyncMock(return_value="ExponentPushToken[test123]")
        
        with patch('notification_service.httpx.AsyncClient') as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            
            mock_client_instance = MagicMock()
            mock_client_instance.post = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client_instance
            
            # Execute
            result = await send_push_notification(
                user_id="user_default",
                title="Test",
                body="Test message"
            )
            
            # Verify
            assert result is False

    @pytest.mark.asyncio
    async def test_send_notification_timeout(self, mock_user_repo):
        """Test handling of timeout errors."""
        # Setup
        mock_user_repo.get_push_token = AsyncMock(return_value="ExponentPushToken[test123]")
        
        with patch('notification_service.httpx.AsyncClient') as mock_client_class:
            mock_client_instance = MagicMock()
            mock_client_instance.post = AsyncMock(
                side_effect=httpx.TimeoutException("Timeout")
            )
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client_instance
            
            # Execute
            result = await send_push_notification(
                user_id="user_default",
                title="Test",
                body="Test message"
            )
            
            # Verify
            assert result is False

    @pytest.mark.asyncio
    async def test_payload_format(self, mock_user_repo):
        """Test that notification payload has correct format."""
        # Setup
        mock_user_repo.get_push_token = AsyncMock(return_value="ExponentPushToken[test123]")
        
        with patch('notification_service.httpx.AsyncClient') as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"data": [{"status": "ok"}]}
            
            mock_post = AsyncMock(return_value=mock_response)
            mock_client_instance = MagicMock()
            mock_client_instance.post = mock_post
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client_instance
            
            # Execute
            await send_push_notification(
                user_id="user_default",
                title="Check-in",
                body="How's it going?",
                data={"session_id": "session_123", "type": "checkin"}
            )
            
            # Verify payload format
            call_args = mock_post.call_args
            assert call_args is not None
            
            payload = call_args.kwargs['json']
            assert payload['to'] == "ExponentPushToken[test123]"
            assert payload['title'] == "Check-in"
            assert payload['body'] == "How's it going?"
            assert payload['data'] == {"session_id": "session_123", "type": "checkin"}
            assert payload['sound'] == "default"
            assert payload['priority'] == "high"

    @pytest.mark.asyncio
    async def test_payload_supports_resume_and_reminder_fields(self, mock_user_repo):
        """Reminder pushes preserve deep-link context fields."""
        mock_user_repo.get_push_token = AsyncMock(return_value="ExponentPushToken[test123]")

        with patch('notification_service.httpx.AsyncClient') as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"data": [{"status": "ok"}]}

            mock_post = AsyncMock(return_value=mock_response)
            mock_client_instance = MagicMock()
            mock_client_instance.post = mock_post
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client_instance

            await send_push_notification(
                user_id="user_default",
                title="Upcoming: Standup",
                body="Standup starts at 09:00 AM.",
                data={
                    "session_id": "session_user_default_2026-02-11",
                    "type": "calendar_reminder",
                    "trigger_type": "calendar_reminder",
                    "calendar_event_id": "gcal_123",
                },
            )

            payload = mock_post.call_args.kwargs["json"]
            assert payload["data"]["session_id"] == "session_user_default_2026-02-11"
            assert payload["data"]["trigger_type"] == "calendar_reminder"
            assert payload["data"]["calendar_event_id"] == "gcal_123"

    @pytest.mark.asyncio
    async def test_delete_push_token(self, mock_user_repo):
        """Test token deletion delegates to user_repo."""
        mock_user_repo.delete_push_token = AsyncMock(return_value=None)

        await delete_push_token("user_default")

        mock_user_repo.delete_push_token.assert_called_once_with("user_default")

    @pytest.mark.asyncio
    async def test_delete_push_token_error(self, mock_user_repo):
        """Test token deletion error handling does not raise."""
        mock_user_repo.delete_push_token = AsyncMock(side_effect=Exception("DB Error"))

        await delete_push_token("user_default")

        mock_user_repo.delete_push_token.assert_called_once_with("user_default")
