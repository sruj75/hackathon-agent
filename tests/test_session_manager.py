"""
Unit tests for ADKSessionManager.

Tests:
- Session creation and retrieval
- Session persistence to/from database
- Daily session ID generation
- Server restart resilience
"""
import pytest

pytest.skip(
    "Legacy SQLAlchemy-era tests skipped after Firestore migration.",
    allow_module_level=True,
)

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from freezegun import freeze_time
from sqlalchemy.ext.asyncio import AsyncSession

from session_manager import ADKSessionManager
from models import UserProfile, AgentSession


class TestSessionManager:
    """Test suite for ADKSessionManager."""

    @pytest.mark.asyncio
    async def test_get_daily_session_id(self):
        """Test deterministic daily session ID generation."""
        with freeze_time("2026-02-04"):
            session_id = ADKSessionManager.get_daily_session_id("user_test")
            assert session_id == "session_user_test_2026-02-04"

    @pytest.mark.asyncio
    async def test_get_daily_session_id_different_dates(self):
        """Test that different dates produce different session IDs."""
        with freeze_time("2026-02-04"):
            session_id_1 = ADKSessionManager.get_daily_session_id("user_test")
        
        with freeze_time("2026-02-05"):
            session_id_2 = ADKSessionManager.get_daily_session_id("user_test")
        
        assert session_id_1 != session_id_2
        assert session_id_1 == "session_user_test_2026-02-04"
        assert session_id_2 == "session_user_test_2026-02-05"

    @pytest.mark.asyncio
    async def test_get_daily_session_id_different_users(self):
        """Test that different users get different session IDs."""
        with freeze_time("2026-02-04"):
            session_id_1 = ADKSessionManager.get_daily_session_id("user_alice")
            session_id_2 = ADKSessionManager.get_daily_session_id("user_bob")
        
        assert session_id_1 != session_id_2
        assert session_id_1 == "session_user_alice_2026-02-04"
        assert session_id_2 == "session_user_bob_2026-02-04"

    @pytest.mark.asyncio
    async def test_get_or_create_session_new(self, test_db, test_user):
        """Test creating a new session when none exists."""
        manager = ADKSessionManager()
        
        session = await manager.get_or_create_session(
            app_name="test_app",
            user_id=test_user.user_id,
            session_id="session_test_new"
        )
        
        assert session is not None
        assert session.id == "session_test_new"
        assert session.user_id == test_user.user_id

    @pytest.mark.asyncio
    async def test_get_or_create_session_from_memory(self, test_db, test_user):
        """Test retrieving an existing session from memory."""
        manager = ADKSessionManager()
        
        # Create session
        session1 = await manager.get_or_create_session(
            app_name="test_app",
            user_id=test_user.user_id,
            session_id="session_test_memory"
        )
        
        # Get same session (should be from RAM)
        session2 = await manager.get_or_create_session(
            app_name="test_app",
            user_id=test_user.user_id,
            session_id="session_test_memory"
        )
        
        assert session1.id == session2.id
        assert session1.id == "session_test_memory"

    @pytest.mark.asyncio
    async def test_save_session_to_db(self, test_db, test_user):
        """Test persisting session state to database."""
        manager = ADKSessionManager()
        session_id = "session_test_save"
        
        state = {
            "user_id": test_user.user_id,
            "date": "2026-02-04",
            "conversation": [{"role": "user", "message": "test"}],
            "current_mode": "PLANNING"
        }
        
        result = await manager.save_agent_session_to_db(
            session_id=session_id,
            state=state,
            user_id=test_user.user_id
        )
        
        assert result is True
        
        # Verify saved to DB
        from repos import session_repo
        async with test_db.bind.connect() as conn:
            async with AsyncSession(conn) as db:
                db_session = await session_repo.get_session(db, session_id)
                assert db_session is not None
                assert db_session.state["current_mode"] == "PLANNING"
                assert len(db_session.state["conversation"]) == 1

    @pytest.mark.asyncio
    async def test_save_session_to_db_without_user_id_in_state(self, test_db, test_user):
        """Test saving session with user_id passed as argument."""
        manager = ADKSessionManager()
        session_id = "session_test_save_no_uid"
        
        state = {
            "date": "2026-02-04",
            "conversation": []
        }
        
        result = await manager.save_agent_session_to_db(
            session_id=session_id,
            state=state,
            user_id=test_user.user_id
        )
        
        assert result is True

    @pytest.mark.asyncio
    async def test_restore_session_from_db(self, test_db, test_user, test_session):
        """Test loading session from database into memory."""
        manager = ADKSessionManager()
        
        # Restore session
        session = await manager.restore_session_from_db(
            app_name="test_app",
            session_id=test_session.session_id,
            user_id=test_user.user_id
        )
        
        assert session is not None
        assert session.id == test_session.session_id
        assert session.user_id == test_user.user_id
        assert session.state["current_mode"] == "PLANNING"

    @pytest.mark.asyncio
    async def test_restore_nonexistent_session(self, test_db, test_user):
        """Test restoring a session that doesn't exist in DB returns None."""
        manager = ADKSessionManager()
        
        session = await manager.restore_session_from_db(
            app_name="test_app",
            session_id="nonexistent_session",
            user_id=test_user.user_id
        )
        
        assert session is None

    @pytest.mark.asyncio
    async def test_get_or_create_session_restores_from_db(self, test_db, test_user, test_session):
        """Test that get_or_create_session restores from DB if not in memory."""
        # Create new manager (empty RAM)
        manager = ADKSessionManager()
        
        # Get session (should restore from DB)
        session = await manager.get_or_create_session(
            app_name="test_app",
            user_id=test_user.user_id,
            session_id=test_session.session_id
        )
        
        assert session is not None
        assert session.id == test_session.session_id
        assert session.state["current_mode"] == "PLANNING"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_session_persistence_across_restarts(self, test_db, test_user):
        """
        Integration test: Simulate server restart.
        
        1. Create session, add state
        2. Save to DB
        3. Simulate restart (new manager instance)
        4. Restore session
        5. Verify state preserved
        """
        # Step 1: Initial manager and session
        manager1 = ADKSessionManager()
        session_id = ADKSessionManager.get_daily_session_id(test_user.user_id)
        
        session1 = await manager1.get_or_create_session(
            app_name="test_app",
            user_id=test_user.user_id,
            session_id=session_id
        )
        
        # Add some state
        session1.state.update({
            "user_id": test_user.user_id,
            "date": "2026-02-04",
            "conversation": [
                {"role": "user", "message": "Hello"},
                {"role": "agent", "message": "Hi there!"}
            ],
            "current_mode": "EXECUTING"
        })
        
        # Step 2: Save to DB
        await manager1.save_agent_session_to_db(
            session_id=session_id,
            state=session1.state,
            user_id=test_user.user_id
        )
        
        # Step 3: Simulate server restart (new manager, empty RAM)
        manager2 = ADKSessionManager()
        
        # Step 4: Get session (should restore from DB)
        session2 = await manager2.get_or_create_session(
            app_name="test_app",
            user_id=test_user.user_id,
            session_id=session_id
        )
        
        # Step 5: Verify state preserved
        assert session2.id == session1.id
        assert session2.state["current_mode"] == "EXECUTING"
        assert len(session2.state["conversation"]) == 2
        assert session2.state["conversation"][0]["message"] == "Hello"
        assert session2.state["conversation"][1]["message"] == "Hi there!"

    @pytest.mark.asyncio
    async def test_multiple_users_separate_sessions(self, test_db):
        """Test that multiple users have isolated sessions."""
        from repos import user_repo
        
        # Create two users
        user1 = await user_repo.create_profile(test_db, "user_alice", "08:00", "22:00")
        user2 = await user_repo.create_profile(test_db, "user_bob", "08:00", "22:00")
        await test_db.commit()
        
        manager = ADKSessionManager()
        
        # Create sessions for both users
        with freeze_time("2026-02-04"):
            session_id_1 = ADKSessionManager.get_daily_session_id(user1.user_id)
            session_id_2 = ADKSessionManager.get_daily_session_id(user2.user_id)
            
            session1 = await manager.get_or_create_session(
                app_name="test_app",
                user_id=user1.user_id,
                session_id=session_id_1
            )
            
            session2 = await manager.get_or_create_session(
                app_name="test_app",
                user_id=user2.user_id,
                session_id=session_id_2
            )
        
        # Verify sessions are different
        assert session1.id != session2.id
        assert session1.user_id != session2.user_id
        assert session1.id == "session_user_alice_2026-02-04"
        assert session2.id == "session_user_bob_2026-02-04"

    @pytest.mark.asyncio
    async def test_save_session_updates_existing(self, test_db, test_user, test_session):
        """Test that saving a session updates existing DB record."""
        manager = ADKSessionManager()
        
        # Update state
        new_state = {
            "user_id": test_user.user_id,
            "date": test_session.date,
            "conversation": [{"role": "user", "message": "updated"}],
            "current_mode": "CLOSED"
        }
        
        result = await manager.save_agent_session_to_db(
            session_id=test_session.session_id,
            state=new_state,
            user_id=test_user.user_id
        )
        
        assert result is True
        
        # Verify update
        from repos import session_repo
        from sqlalchemy.ext.asyncio import AsyncSession
        async with test_db.bind.connect() as conn:
            async with AsyncSession(conn) as db:
                db_session = await session_repo.get_session(db, test_session.session_id)
                assert db_session.state["current_mode"] == "CLOSED"
                assert db_session.state["conversation"][0]["message"] == "updated"

    @pytest.mark.asyncio
    async def test_session_state_contains_date_automatically(self, test_db, test_user):
        """Test that saving session without date in state adds it automatically."""
        manager = ADKSessionManager()
        session_id = "session_test_auto_date"
        
        state = {
            "user_id": test_user.user_id,
            "conversation": []
            # No 'date' field
        }
        
        with freeze_time("2026-02-04"):
            result = await manager.save_agent_session_to_db(
                session_id=session_id,
                state=state,
                user_id=test_user.user_id
            )
        
        assert result is True
        
        # Verify date was added
        from repos import session_repo
        async with test_db.bind.connect() as conn:
            async with AsyncSession(conn) as db:
                db_session = await session_repo.get_session(db, session_id)
                assert db_session is not None
                assert db_session.date == "2026-02-04"

    @pytest.mark.asyncio
    async def test_manager_with_custom_service(self):
        """Test initializing manager with custom InMemorySessionService."""
        from google.adk.sessions import InMemorySessionService
        
        custom_service = InMemorySessionService()
        manager = ADKSessionManager(service=custom_service)
        
        assert manager.service is custom_service

    @pytest.mark.asyncio
    async def test_manager_without_service_creates_default(self):
        """Test that manager creates default service if none provided."""
        manager = ADKSessionManager()
        
        assert manager.service is not None
        from google.adk.sessions import InMemorySessionService
        assert isinstance(manager.service, InMemorySessionService)
