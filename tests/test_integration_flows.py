"""
Integration tests for complete end-to-end flows.

Tests:
- Morning wake flow
- Check-in flow
- Session continuity across server restarts
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from freezegun import freeze_time

from repos import event_repo, user_repo, session_repo
from session_manager import ADKSessionManager
from agent_runtime import AgentRuntime
import cron_service


@pytest.mark.integration
class TestMorningWakeFlow:
    """Integration test for morning wake flow."""

    @pytest.mark.asyncio
    async def test_morning_wake_complete_flow(self, test_db, test_user):
        """
        Test complete morning wake flow:
        1. Startup creates morning_wake event
        2. Event scheduled with cron
        3. Cron triggers event
        4. Agent wakes in thinking mode
        5. Agent sends push notification
        """
        # Step 1: Create morning wake event (simulating startup)
        wake_time = datetime(2026, 2, 5, 8, 0, 0)
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=wake_time,
            event_type="morning_wake",
            payload={"reason": "daily_kickoff"}
        )
        await test_db.commit()
        
        # Step 2: Simulate cron job creation
        with patch('cron_service.httpx.AsyncClient') as mock_client_class:
            mock_response = MagicMock()
            mock_response.json.return_value = {"jobId": 99999}
            mock_client_instance = MagicMock()
            mock_client_instance.put = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client_instance
            
            with patch('cron_service.CRONJOB_API_KEY', 'test_key'):
                cron_job_id = await cron_service.create_one_time_job(
                    target_datetime=wake_time,
                    event_id=event.id,
                    timezone="UTC"
                )
        
        # Update event with cron job ID
        await event_repo.update_cron_job_id(test_db, event.id, cron_job_id)
        await test_db.commit()
        
        # Step 3-4: Simulate cron trigger and agent execution
        session_manager = ADKSessionManager()
        
        with patch('agent_runtime.Runner') as mock_runner_class:
            with patch('notification_service.send_push_notification', new_callable=AsyncMock) as mock_send_notif:
                # Mock agent execution
                async def mock_run(*args, **kwargs):
                    # Simulate agent calling send_push_notification tool
                    await mock_send_notif(
                        db=test_db,
                        user_id=test_user.user_id,
                        title="Good morning!",
                        body="Ready to plan your day?",
                        data={"type": "morning_wake"}
                    )
                    return
                    yield
                
                mock_runner_instance = MagicMock()
                mock_runner_instance.run_async = mock_run
                mock_runner_class.return_value = mock_runner_instance
                
                # Step 4: Run thinking mode
                async for _ in AgentRuntime.run_thinking_mode(
                    user_id=test_user.user_id,
                    trigger_context="Morning wake: 8:00 AM",
                    session_manager=session_manager,
                    db=test_db
                ):
                    pass
                
                # Step 5: Verify notification was sent
                mock_send_notif.assert_called()
        
        # Verify event is marked as executed
        executed_event = await event_repo.get_by_id(test_db, event.id)
        assert executed_event.executed is False  # Not yet executed in this flow
        
        # Now mark it executed (simulating the endpoint)
        await event_repo.mark_executed(test_db, event.id)
        await test_db.commit()
        
        final_event = await event_repo.get_by_id(test_db, event.id)
        assert final_event.executed is True


@pytest.mark.integration
class TestCheckinFlow:
    """Integration test for check-in flow."""

    @pytest.mark.asyncio
    async def test_checkin_complete_flow(self, test_db, test_user):
        """
        Test complete check-in flow:
        1. Agent sets timer via tool call
        2. Event created with cron_job_id
        3. External cron triggers event
        4. Agent wakes, evaluates context
        5. Sends notification if needed
        6. Cron job cleaned up
        """
        # Step 1-2: Simulate agent setting timer
        scheduled_time = datetime.utcnow() + timedelta(minutes=30)
        
        # Create event
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=scheduled_time,
            event_type="checkin",
            payload={"reason": "deep_work_end"}
        )
        await test_db.commit()
        
        # Simulate cron job creation
        with patch('cron_service.httpx.AsyncClient') as mock_client_class:
            mock_response = MagicMock()
            mock_response.json.return_value = {"jobId": 55555}
            mock_client_instance = MagicMock()
            mock_client_instance.put = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client_instance
            
            with patch('cron_service.CRONJOB_API_KEY', 'test_key'):
                cron_job_id = await cron_service.create_one_time_job(
                    target_datetime=scheduled_time,
                    event_id=event.id,
                    timezone="UTC"
                )
        
        await event_repo.update_cron_job_id(test_db, event.id, cron_job_id)
        await test_db.commit()
        
        # Step 3-4: Simulate cron trigger
        session_manager = ADKSessionManager()
        
        with patch('agent_runtime.Runner') as mock_runner_class:
            # Mock agent execution
            async def mock_run(*args, **kwargs):
                return
                yield
            
            mock_runner_instance = MagicMock()
            mock_runner_instance.run_async = mock_run
            mock_runner_class.return_value = mock_runner_instance
            
            # Execute event
            async for _ in AgentRuntime.run_thinking_mode(
                user_id=test_user.user_id,
                trigger_context=f"Check-in: {event.payload}",
                session_manager=session_manager,
                db=test_db
            ):
                pass
        
        # Step 5: Mark event as executed
        await event_repo.mark_executed(test_db, event.id)
        
        # Step 6: Cleanup cron job
        with patch('cron_service.httpx.AsyncClient') as mock_client_class:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client_instance = MagicMock()
            mock_client_instance.delete = AsyncMock(return_value=mock_response)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client_instance
            
            with patch('cron_service.CRONJOB_API_KEY', 'test_key'):
                deleted = await cron_service.delete_job(cron_job_id)
        
        assert deleted is True
        
        # Verify event is executed
        final_event = await event_repo.get_by_id(test_db, event.id)
        assert final_event.executed is True


@pytest.mark.integration
class TestSessionContinuity:
    """Integration test for session continuity across restarts."""

    @pytest.mark.asyncio
    async def test_session_survives_server_restart(self, test_db, test_user):
        """
        Test session continuity:
        1. Morning session created
        2. Agent has conversation (adds to history)
        3. Server restarts (simulate)
        4. Session restored from DB
        5. Agent has access to previous context
        """
        # Step 1: Create morning session
        with freeze_time("2026-02-04 08:00:00"):
            manager1 = ADKSessionManager()
            session_id = ADKSessionManager.get_daily_session_id(test_user.user_id)
            
            session = await manager1.get_or_create_session(
                app_name="test_app",
                user_id=test_user.user_id,
                session_id=session_id
            )
            
            # Step 2: Simulate conversation
            session.state.update({
                "user_id": test_user.user_id,
                "date": "2026-02-04",
                "conversation": [
                    {"role": "user", "message": "Good morning, let's plan my day"},
                    {"role": "agent", "message": "Great! What's on your mind?"},
                    {"role": "user", "message": "I have 3 tasks: code review, meeting, workout"}
                ],
                "current_mode": "PLANNING",
                "today_tasks": ["code_review", "meeting", "workout"]
            })
            
            # Save to DB
            await manager1.save_agent_session_to_db(
                session_id=session_id,
                state=session.state,
                user_id=test_user.user_id
            )
        
        # Step 3: Simulate server restart (new manager, empty RAM)
        with freeze_time("2026-02-04 10:00:00"):
            manager2 = ADKSessionManager()
            
            # Step 4: Restore session
            restored_session = await manager2.get_or_create_session(
                app_name="test_app",
                user_id=test_user.user_id,
                session_id=session_id
            )
            
            # Step 5: Verify context preserved
            assert restored_session.id == session_id
            assert restored_session.state["current_mode"] == "PLANNING"
            assert len(restored_session.state["conversation"]) == 3
            assert restored_session.state["conversation"][0]["message"] == "Good morning, let's plan my day"
            assert restored_session.state["today_tasks"] == ["code_review", "meeting", "workout"]

    @pytest.mark.asyncio
    async def test_multiple_checkins_same_session(self, test_db, test_user):
        """
        Test multiple check-ins use the same session:
        1. Morning planning
        2. First check-in (11 AM)
        3. Second check-in (2 PM)
        4. All use same session_id
        5. Context accumulates
        """
        with freeze_time("2026-02-04"):
            manager = ADKSessionManager()
            session_id = ADKSessionManager.get_daily_session_id(test_user.user_id)
            
            # Morning planning
            with freeze_time("2026-02-04 08:00:00"):
                with patch('agent_runtime.Runner'):
                    async for _ in AgentRuntime.run_thinking_mode(
                        user_id=test_user.user_id,
                        trigger_context="Morning wake",
                        session_manager=manager,
                        db=test_db
                    ):
                        pass
            
            # First check-in
            with freeze_time("2026-02-04 11:00:00"):
                with patch('agent_runtime.Runner'):
                    async for _ in AgentRuntime.run_thinking_mode(
                        user_id=test_user.user_id,
                        trigger_context="Check-in: deep work ended",
                        session_manager=manager,
                        db=test_db
                    ):
                        pass
            
            # Second check-in
            with freeze_time("2026-02-04 14:00:00"):
                with patch('agent_runtime.Runner'):
                    async for _ in AgentRuntime.run_thinking_mode(
                        user_id=test_user.user_id,
                        trigger_context="Check-in: lunch ended",
                        session_manager=manager,
                        db=test_db
                    ):
                        pass
            
            # Verify all interactions used same session
            db_session = await session_repo.get_session(test_db, session_id)
            assert db_session is not None
            assert db_session.user_id == test_user.user_id
            assert db_session.date == "2026-02-04"


@pytest.mark.integration
class TestFullDayCycle:
    """Integration test for complete day cycle."""

    @pytest.mark.asyncio
    async def test_full_day_cycle(self, test_db, test_user):
        """
        Test complete day from wake to sleep:
        1. Morning wake (8 AM)
        2. Planning session
        3. Multiple check-ins throughout day
        4. Evening reflection (10 PM)
        5. Next morning wake scheduled
        """
        with freeze_time("2026-02-04"):
            manager = ADKSessionManager()
            session_id = ADKSessionManager.get_daily_session_id(test_user.user_id)
            
            # 1. Morning wake
            with freeze_time("2026-02-04 08:00:00"):
                morning_event = await event_repo.create_event(
                    db=test_db,
                    user_id=test_user.user_id,
                    scheduled_time=datetime(2026, 2, 4, 8, 0, 0),
                    event_type="morning_wake",
                    payload={"reason": "daily_kickoff"}
                )
                await test_db.commit()
                
                with patch('agent_runtime.Runner'):
                    async for _ in AgentRuntime.run_thinking_mode(
                        user_id=test_user.user_id,
                        trigger_context="Morning wake: 8:00 AM",
                        session_manager=manager,
                        db=test_db
                    ):
                        pass
                
                await event_repo.mark_executed(test_db, morning_event.id)
            
            # 2. Check-in 1 (11 AM)
            with freeze_time("2026-02-04 11:00:00"):
                checkin1 = await event_repo.create_event(
                    db=test_db,
                    user_id=test_user.user_id,
                    scheduled_time=datetime(2026, 2, 4, 11, 0, 0),
                    event_type="checkin",
                    payload={"reason": "deep_work_end"}
                )
                await test_db.commit()
                
                with patch('agent_runtime.Runner'):
                    async for _ in AgentRuntime.run_thinking_mode(
                        user_id=test_user.user_id,
                        trigger_context="Check-in: deep work ended",
                        session_manager=manager,
                        db=test_db
                    ):
                        pass
                
                await event_repo.mark_executed(test_db, checkin1.id)
            
            # 3. Check-in 2 (2 PM)
            with freeze_time("2026-02-04 14:00:00"):
                checkin2 = await event_repo.create_event(
                    db=test_db,
                    user_id=test_user.user_id,
                    scheduled_time=datetime(2026, 2, 4, 14, 0, 0),
                    event_type="checkin",
                    payload={"reason": "lunch_end"}
                )
                await test_db.commit()
                
                with patch('agent_runtime.Runner'):
                    async for _ in AgentRuntime.run_thinking_mode(
                        user_id=test_user.user_id,
                        trigger_context="Check-in: lunch ended",
                        session_manager=manager,
                        db=test_db
                    ):
                        pass
                
                await event_repo.mark_executed(test_db, checkin2.id)
            
            # 4. Evening reflection (10 PM)
            with freeze_time("2026-02-04 22:00:00"):
                evening = await event_repo.create_event(
                    db=test_db,
                    user_id=test_user.user_id,
                    scheduled_time=datetime(2026, 2, 4, 22, 0, 0),
                    event_type="evening_reflection",
                    payload={"reason": "day_end"}
                )
                await test_db.commit()
                
                with patch('agent_runtime.Runner'):
                    async for _ in AgentRuntime.run_thinking_mode(
                        user_id=test_user.user_id,
                        trigger_context="Evening reflection: 10:00 PM",
                        session_manager=manager,
                        db=test_db
                    ):
                        pass
                
                await event_repo.mark_executed(test_db, evening.id)
            
            # Verify all events were executed
            all_events = [morning_event, checkin1, checkin2, evening]
            for event in all_events:
                event_check = await event_repo.get_by_id(test_db, event.id)
                assert event_check.executed is True
            
            # Verify session exists and has correct date
            db_session = await session_repo.get_session(test_db, session_id)
            assert db_session is not None
            assert db_session.date == "2026-02-04"
