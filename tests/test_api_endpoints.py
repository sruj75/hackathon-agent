"""
Unit tests for FastAPI endpoints.

Tests:
- POST /api/save-token
- POST /api/execute-event/{event_id}
- GET /health
- GET /
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timedelta

# Import the app
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import app, get_db
from models import ScheduledEvent, UserProfile


@pytest.fixture(autouse=True)
def setup_dependency_overrides(test_db):
    app.dependency_overrides[get_db] = lambda: test_db
    yield
    app.dependency_overrides.clear()


class TestHealthEndpoints:
    """Test suite for health check endpoints."""

    def test_root_endpoint(self):
        """Test root endpoint returns status."""
        client = TestClient(app)
        response = client.get("/")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "app" in data
        assert "agent" in data

    def test_health_endpoint(self):
        """Test health check endpoint."""
        client = TestClient(app)
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestSaveTokenEndpoint:
    """Test suite for POST /api/save-token."""

    @pytest.mark.asyncio
    async def test_save_token_success(self, test_db, test_user):
        """Test successful token save."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/save-token",
                json={
                    "user_id": test_user.user_id,
                    "token": "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]"
                }
            )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "saved"
        assert data["user_id"] == test_user.user_id

    @pytest.mark.asyncio
    async def test_save_token_missing_user_id(self, test_db):
        """Test that missing user_id returns 400."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/save-token",
                json={
                    "token": "ExponentPushToken[test]"
                }
            )
        
        assert response.status_code == 400
        assert "user_id" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_save_token_missing_token(self, test_db, test_user):
        """Test that missing token returns 400."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/save-token",
                json={
                    "user_id": test_user.user_id
                }
            )
        
        assert response.status_code == 400
        assert "token" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_save_token_invalid_format(self, test_db, test_user):
        """Test that invalid token format returns 400."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/save-token",
                json={
                    "user_id": test_user.user_id,
                    "token": "invalid_token_format"
                }
            )
        
        assert response.status_code == 400
        assert "Invalid token format" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_save_token_valid_formats(self, test_db, test_user):
        """Test various valid Expo token formats."""
        valid_tokens = [
            "ExponentPushToken[abc123]",
            "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]",
            "ExponentPushToken[A1B2C3D4E5]"
        ]
        
        for token in valid_tokens:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/api/save-token",
                    json={
                        "user_id": test_user.user_id,
                        "token": token
                    }
                )
            
            assert response.status_code == 200, f"Failed for token: {token}"


class TestExecuteEventEndpoint:
    """Test suite for POST /api/execute-event/{event_id}."""

    @pytest.mark.asyncio
    async def test_execute_event_success(self, test_db, test_user, test_event):
        """Test successful event execution."""
        # Mock AgentRuntime to avoid actual agent execution
        with patch('main.AgentRuntime.run_thinking_mode') as mock_thinking_mode:
            # Mock empty async generator
            async def mock_run(*args, **kwargs):
                yield MagicMock()
            mock_thinking_mode.return_value = mock_run()
            
            with patch('main.cron_service.delete_job', new_callable=AsyncMock):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    response = await ac.post(f"/api/execute-event/{test_event.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "executed"

    @pytest.mark.asyncio
    async def test_execute_event_not_found(self, test_db):
        """Test that nonexistent event returns 404."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/api/execute-event/nonexistent_event_id")
        
        assert response.status_code == 404
        assert "Event not found" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_execute_event_already_executed(self, test_db, test_user):
        """Test that already executed event returns success without re-execution."""
        from repos import event_repo
        
        # Create an already executed event
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=datetime.utcnow(),
            event_type="checkin",
            payload={"reason": "test"}
        )
        await event_repo.mark_executed(db=test_db, event_id=event.id)
        await test_db.commit()
        
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(f"/api/execute-event/{event.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "already_executed"

    @pytest.mark.asyncio
    async def test_execute_event_marks_executed(self, test_db, test_user, test_event):
        """Test that event is marked as executed after processing."""
        from repos import event_repo
        
        # Verify event starts as not executed
        assert test_event.executed is False
        
        with patch('main.AgentRuntime.run_thinking_mode') as mock_thinking_mode:
            async def mock_run(*args, **kwargs):
                yield MagicMock()
            mock_thinking_mode.return_value = mock_run()
            
            with patch('main.cron_service.delete_job', new_callable=AsyncMock):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    await ac.post(f"/api/execute-event/{test_event.id}")
        
        # Verify event is now marked as executed
        # Need to refresh the event object from the same session or query it again
        await test_db.refresh(test_event)
        assert test_event.executed is True

    @pytest.mark.asyncio
    async def test_execute_event_deletes_cron_job(self, test_db, test_user):
        """Test that cron job is deleted after event execution."""
        from repos import event_repo
        
        # Create event with cron job ID
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=datetime.utcnow(),
            event_type="checkin",
            payload={"reason": "test"},
            cron_job_id=12345
        )
        await test_db.commit()
        
        with patch('main.AgentRuntime.run_thinking_mode') as mock_thinking_mode:
            async def mock_run(*args, **kwargs):
                yield MagicMock()
            mock_thinking_mode.return_value = mock_run()
            
            with patch('main.cron_service.delete_job', new_callable=AsyncMock) as mock_delete:
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    await ac.post(f"/api/execute-event/{event.id}")
                
                # Verify cron job deletion was attempted
                mock_delete.assert_called_once_with(12345)

    @pytest.mark.asyncio
    async def test_execute_event_handles_cron_cleanup_failure(self, test_db, test_user):
        """Test that cron cleanup failure doesn't fail the request."""
        from repos import event_repo
        
        # Create event with cron job ID
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=datetime.utcnow(),
            event_type="checkin",
            payload={"reason": "test"},
            cron_job_id=12345
        )
        await test_db.commit()
        
        with patch('main.AgentRuntime.run_thinking_mode') as mock_thinking_mode:
            async def mock_run(*args, **kwargs):
                yield MagicMock()
            mock_thinking_mode.return_value = mock_run()
            
            with patch('main.cron_service.delete_job', side_effect=Exception("Cleanup failed")):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    response = await ac.post(f"/api/execute-event/{event.id}")
        
        # Request should still succeed even if cleanup fails
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_execute_event_calls_agent_thinking_mode(self, test_db, test_user, test_event):
        """Test that event execution calls AgentRuntime.run_thinking_mode."""
        with patch('main.AgentRuntime.run_thinking_mode') as mock_thinking_mode:
            async def mock_run(*args, **kwargs):
                yield MagicMock()
            mock_thinking_mode.return_value = mock_run()
            
            with patch('main.cron_service.delete_job', new_callable=AsyncMock):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    await ac.post(f"/api/execute-event/{test_event.id}")
            
            # Verify thinking mode was called
            mock_thinking_mode.assert_called_once()
            call_kwargs = mock_thinking_mode.call_args.kwargs
            assert call_kwargs['user_id'] == test_user.user_id
            assert 'trigger_context' in call_kwargs

    @pytest.mark.asyncio
    async def test_execute_event_handles_agent_failure(self, test_db, test_user, test_event):
        """Test that agent failure doesn't prevent event from being marked executed."""
        with patch('main.AgentRuntime.run_thinking_mode') as mock_thinking_mode:
            # Mock agent failure
            async def mock_run(*args, **kwargs):
                raise Exception("Agent failed")
                yield
            mock_thinking_mode.return_value = mock_run()
            
            with patch('main.cron_service.delete_job', new_callable=AsyncMock):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    response = await ac.post(f"/api/execute-event/{test_event.id}")
        
        # Request should still succeed
        assert response.status_code == 200
        
        # Event should still be marked as executed
        await test_db.refresh(test_event)
        assert test_event.executed is True

    @pytest.mark.asyncio
    async def test_execute_event_idempotency(self, test_db, test_user, test_event):
        """Test that executing the same event multiple times is safe."""
        with patch('main.AgentRuntime.run_thinking_mode') as mock_thinking_mode:
            async def mock_run(*args, **kwargs):
                yield MagicMock()
            mock_thinking_mode.return_value = mock_run()
            
            with patch('main.cron_service.delete_job', new_callable=AsyncMock):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    # Execute once
                    response1 = await ac.post(f"/api/execute-event/{test_event.id}")
                    assert response1.status_code == 200
                    
                    # Execute again
                    response2 = await ac.post(f"/api/execute-event/{test_event.id}")
                    assert response2.status_code == 200
                    assert response2.json()["status"] == "already_executed"

    @pytest.mark.asyncio
    async def test_execute_event_trigger_context_format(self, test_db, test_user, test_event):
        """Test that execute_event uses the phase-4 minimal trigger context."""
        captured_context = None
        
        async def mock_run(user_id, trigger_context, **kwargs):
            nonlocal captured_context
            captured_context = trigger_context
            yield MagicMock()
        
        with patch('main.AgentRuntime.run_thinking_mode', side_effect=mock_run):
            with patch('main.cron_service.delete_job', new_callable=AsyncMock):
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                    await ac.post(f"/api/execute-event/{test_event.id}")
        
        # Regression guard:
        # Phase 4 intentionally uses a minimal trigger prompt and lets the
        # agent gather context via tools.
        assert captured_context is not None
        assert captured_context == "You just woke up."
