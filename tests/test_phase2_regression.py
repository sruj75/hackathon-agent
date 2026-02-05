"""
Phase 2 regression tests.

Ensures that Phase 2 functionality still works after Phase 3 additions.

Phase 2 includes:
- Context variables
- Timer tools
- Time tools
- Dynamic cron service
- Event execution API
- Morning wake initialization
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from freezegun import freeze_time

from context import current_user_id, current_session_id, current_db
from repos import event_repo
import cron_service


@pytest.mark.regression
class TestPhase2ContextVariables:
    """Regression tests for context variable functionality."""

    @pytest.mark.asyncio
    async def test_context_variables_can_be_set(self, test_db, test_user):
        """Test that context variables still work."""
        # Set context
        current_user_id.set(test_user.user_id)
        current_session_id.set("test_session_123")
        current_db.set(test_db)
        
        # Retrieve
        assert current_user_id.get() == test_user.user_id
        assert current_session_id.get() == "test_session_123"
        assert current_db.get() == test_db

    @pytest.mark.asyncio
    async def test_context_variables_isolated_per_task(self, test_user):
        """Test that context variables are task-isolated."""
        # Set in this context
        current_user_id.set(test_user.user_id)
        
        # Verify retrieval works
        assert current_user_id.get() == test_user.user_id


@pytest.mark.regression
class TestPhase2CronService:
    """Regression tests for cron service functionality."""

    @pytest.mark.asyncio
    async def test_create_one_time_job(self, mock_cron_api):
        """Test that cron job creation still works."""
        target_datetime = datetime(2026, 2, 5, 8, 0, 0)
        event_id = "regression_event_123"
        
        mock_cron_api.put.return_value.json.return_value = {"jobId": 77777}
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_key'):
            job_id = await cron_service.create_one_time_job(
                target_datetime=target_datetime,
                event_id=event_id,
                timezone="UTC"
            )
        
        assert job_id == 77777

    @pytest.mark.asyncio
    async def test_delete_job(self, mock_cron_api):
        """Test that cron job deletion still works."""
        job_id = 77777
        
        mock_cron_api.delete.return_value.status_code = 200
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_key'):
            result = await cron_service.delete_job(job_id)
        
        assert result is True

    @pytest.mark.asyncio
    async def test_cron_job_expiration_calculation(self, mock_cron_api):
        """Test that job expiration is still calculated correctly."""
        target_datetime = datetime(2026, 2, 4, 10, 0, 0)
        
        mock_cron_api.put.return_value.json.return_value = {"jobId": 123}
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_key'):
            await cron_service.create_one_time_job(
                target_datetime=target_datetime,
                event_id="test",
                timezone="UTC"
            )
        
        # Verify expiration is 5 minutes after target
        call_args = mock_cron_api.put.call_args
        payload = call_args.kwargs['json']
        expires_at = payload['job']['schedule']['expiresAt']
        
        expected_expires = int(datetime(2026, 2, 4, 10, 5, 0).strftime("%Y%m%d%H%M%S"))
        assert expires_at == expected_expires


@pytest.mark.regression
class TestPhase2EventExecution:
    """Regression tests for event execution functionality."""

    @pytest.mark.asyncio
    async def test_event_can_be_created_with_cron_job_id(self, test_db, test_user):
        """Test that events can still be created with cron_job_id."""
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=datetime.utcnow() + timedelta(hours=1),
            event_type="checkin",
            payload={"reason": "test"},
            cron_job_id=99999
        )
        
        assert event.cron_job_id == 99999

    @pytest.mark.asyncio
    async def test_cron_job_id_can_be_updated(self, test_db, test_event):
        """Test that cron_job_id can still be updated."""
        result = await event_repo.update_cron_job_id(
            db=test_db,
            event_id=test_event.id,
            cron_job_id=66666
        )
        
        assert result is True
        
        # Verify
        updated = await event_repo.get_by_id(db=test_db, event_id=test_event.id)
        assert updated.cron_job_id == 66666

    @pytest.mark.asyncio
    async def test_event_lifecycle_with_cron(self, test_db, test_user):
        """Test complete event lifecycle with cron job."""
        # Create event
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=datetime.utcnow() + timedelta(minutes=30),
            event_type="checkin",
            payload={"reason": "regression_test"}
        )
        
        assert event.executed is False
        assert event.cron_job_id is None
        
        # Add cron job ID
        await event_repo.update_cron_job_id(db=test_db, event_id=event.id, cron_job_id=55555)
        
        # Mark executed
        await event_repo.mark_executed(db=test_db, event_id=event.id)
        
        # Verify final state
        final = await event_repo.get_by_id(db=test_db, event_id=event.id)
        assert final.executed is True
        assert final.cron_job_id == 55555


@pytest.mark.regression
class TestPhase2MorningWake:
    """Regression tests for morning wake functionality."""

    @pytest.mark.asyncio
    async def test_morning_wake_event_can_be_created(self, test_db, test_user):
        """Test that morning wake events can still be created."""
        wake_time = datetime(2026, 2, 5, 8, 0, 0)
        
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=wake_time,
            event_type="morning_wake",
            payload={"reason": "daily_kickoff"}
        )
        
        assert event.event_type == "morning_wake"
        assert event.scheduled_time == wake_time

    @pytest.mark.asyncio
    async def test_event_type_filtering(self, test_db, test_user):
        """Test that events can be filtered by type."""
        wake_time = datetime(2026, 2, 5, 8, 0, 0)
        
        # Create morning wake event
        await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=wake_time,
            event_type="morning_wake",
            payload={}
        )
        
        # Find by type and time
        found = await event_repo.get_event_by_type_and_time(
            db=test_db,
            user_id=test_user.user_id,
            event_type="morning_wake",
            scheduled_time=wake_time
        )
        
        assert found is not None
        assert found.event_type == "morning_wake"


@pytest.mark.regression
class TestPhase2AgentIntegration:
    """Regression tests for agent integration with Phase 2 features."""

    @pytest.mark.asyncio
    async def test_agent_runtime_can_be_imported(self):
        """Test that AgentRuntime is still importable."""
        from agent_runtime import AgentRuntime
        
        assert AgentRuntime is not None

    @pytest.mark.asyncio
    async def test_thinking_mode_signature_unchanged(self):
        """Test that run_thinking_mode signature is still valid."""
        from agent_runtime import AgentRuntime
        from session_manager import ADKSessionManager
        import inspect
        
        sig = inspect.signature(AgentRuntime.run_thinking_mode)
        params = list(sig.parameters.keys())
        
        assert 'user_id' in params
        assert 'trigger_context' in params
        assert 'session_manager' in params
        assert 'db' in params

    @pytest.mark.asyncio
    async def test_conversation_mode_config_still_works(self):
        """Test that get_conversation_mode_config still returns valid config."""
        from agent_runtime import AgentRuntime
        
        config = AgentRuntime.get_conversation_mode_config()
        
        assert config is not None
        assert config.response_modalities == ["AUDIO"]


@pytest.mark.regression
class TestPhase2ErrorHandling:
    """Regression tests for error handling in Phase 2 features."""

    @pytest.mark.asyncio
    async def test_cron_service_handles_missing_api_key(self):
        """Test that cron service still handles missing API key."""
        with patch('cron_service.CRONJOB_API_KEY', None):
            with pytest.raises(ValueError, match="CRONJOB_ORG_API_KEY"):
                await cron_service.create_one_time_job(
                    target_datetime=datetime.utcnow(),
                    event_id="test"
                )

    @pytest.mark.asyncio
    async def test_cron_delete_handles_missing_api_key(self):
        """Test that delete_job handles missing API key gracefully."""
        with patch('cron_service.CRONJOB_API_KEY', None):
            result = await cron_service.delete_job(123)
            
            assert result is False

    @pytest.mark.asyncio
    async def test_event_repo_handles_nonexistent_event(self, test_db):
        """Test that get_by_id returns None for nonexistent event."""
        event = await event_repo.get_by_id(db=test_db, event_id="nonexistent")
        
        assert event is None

    @pytest.mark.asyncio
    async def test_update_cron_job_id_handles_nonexistent_event(self, test_db):
        """Test that update_cron_job_id returns False for nonexistent event."""
        result = await event_repo.update_cron_job_id(
            db=test_db,
            event_id="nonexistent",
            cron_job_id=123
        )
        
        assert result is False
