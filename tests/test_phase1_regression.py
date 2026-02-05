"""
Phase 1 regression tests.

Ensures that Phase 1 functionality still works after Phase 3 additions.

Phase 1 includes:
- Database foundation (SQLAlchemy models, migrations, repos)
- ADK session manager
- Basic CRUD operations
"""
import pytest
from datetime import datetime
from freezegun import freeze_time

from repos import user_repo, session_repo, event_repo
from session_manager import ADKSessionManager
from models import UserProfile, UserPushToken, AgentSession, ScheduledEvent


@pytest.mark.regression
class TestPhase1UserRepository:
    """Regression tests for user repository functionality."""

    @pytest.mark.asyncio
    async def test_user_profile_crud_operations(self, test_db):
        """Test that basic user profile CRUD still works."""
        # Create
        user = await user_repo.create_profile(
            db=test_db,
            user_id="regression_user_1",
            wake_time="07:30",
            bedtime="23:00",
            timezone="America/Los_Angeles",
            health_anchors=["sleep", "meals", "exercise"]
        )
        
        assert user.user_id == "regression_user_1"
        assert user.wake_time == "07:30"
        
        # Read
        retrieved = await user_repo.get_profile(db=test_db, user_id="regression_user_1")
        assert retrieved is not None
        assert retrieved.wake_time == "07:30"
        
        # Update
        updated = await user_repo.update_profile(
            db=test_db,
            user_id="regression_user_1",
            wake_time="06:00"
        )
        assert updated.wake_time == "06:00"
        
        # Verify update persisted
        final = await user_repo.get_profile(db=test_db, user_id="regression_user_1")
        assert final.wake_time == "06:00"

    @pytest.mark.asyncio
    async def test_push_token_operations(self, test_db, test_user):
        """Test that push token storage still works."""
        # Save token
        token = "ExponentPushToken[regression_test_123]"
        await user_repo.save_push_token(
            db=test_db,
            user_id=test_user.user_id,
            token=token
        )
        
        # Retrieve token
        retrieved = await user_repo.get_push_token(db=test_db, user_id=test_user.user_id)
        assert retrieved == token
        
        # Update token (upsert)
        new_token = "ExponentPushToken[regression_test_456]"
        await user_repo.save_push_token(
            db=test_db,
            user_id=test_user.user_id,
            token=new_token
        )
        
        # Verify upsert worked
        final_token = await user_repo.get_push_token(db=test_db, user_id=test_user.user_id)
        assert final_token == new_token
        
        # Delete token
        await user_repo.delete_push_token(db=test_db, user_id=test_user.user_id)
        deleted_token = await user_repo.get_push_token(db=test_db, user_id=test_user.user_id)
        assert deleted_token is None

    @pytest.mark.asyncio
    async def test_get_all_users(self, test_db):
        """Test that getting all users still works."""
        # Create multiple users
        for i in range(3):
            await user_repo.create_profile(
                db=test_db,
                user_id=f"regression_user_{i}",
                wake_time="08:00",
                bedtime="22:00"
            )
        
        # Get all users
        users = await user_repo.get_all_users(db=test_db)
        
        assert len(users) >= 3
        user_ids = [u.user_id for u in users]
        assert "regression_user_0" in user_ids
        assert "regression_user_1" in user_ids
        assert "regression_user_2" in user_ids


@pytest.mark.regression
class TestPhase1SessionRepository:
    """Regression tests for session repository functionality."""

    @pytest.mark.asyncio
    async def test_session_save_and_retrieve(self, test_db, test_user):
        """Test that session persistence still works."""
        session_id = "regression_session_1"
        state = {
            "user_id": test_user.user_id,
            "date": "2026-02-04",
            "conversation": [{"role": "user", "message": "test"}],
            "current_mode": "PLANNING"
        }
        
        # Save
        await session_repo.save_session(
            db=test_db,
            session_id=session_id,
            state=state
        )
        
        # Retrieve
        retrieved = await session_repo.get_session(db=test_db, session_id=session_id)
        
        assert retrieved is not None
        assert retrieved.session_id == session_id
        assert retrieved.state["current_mode"] == "PLANNING"

    @pytest.mark.asyncio
    async def test_get_today_session(self, test_db, test_user):
        """Test that date-based session lookup still works."""
        session_id = "regression_session_date"
        date = "2026-02-04"
        
        await session_repo.upsert_session(
            db=test_db,
            session_id=session_id,
            user_id=test_user.user_id,
            date=date,
            state={"test": "data"}
        )
        
        # Retrieve by user and date
        retrieved = await session_repo.get_today_session(
            db=test_db,
            user_id=test_user.user_id,
            date=date
        )
        
        assert retrieved is not None
        assert retrieved.session_id == session_id
        assert retrieved.date == date


@pytest.mark.regression
class TestPhase1EventRepository:
    """Regression tests for event repository functionality."""

    @pytest.mark.asyncio
    async def test_event_creation_and_retrieval(self, test_db, test_user):
        """Test that event CRUD still works."""
        from datetime import timedelta
        
        scheduled_time = datetime.utcnow() + timedelta(hours=1)
        payload = {"reason": "regression_test"}
        
        # Create
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=scheduled_time,
            event_type="checkin",
            payload=payload
        )
        
        assert event is not None
        assert event.user_id == test_user.user_id
        assert event.executed is False
        
        # Retrieve
        retrieved = await event_repo.get_by_id(db=test_db, event_id=event.id)
        
        assert retrieved is not None
        assert retrieved.id == event.id
        assert retrieved.payload == payload

    @pytest.mark.asyncio
    async def test_mark_event_executed(self, test_db, test_event):
        """Test that marking events executed still works."""
        assert test_event.executed is False
        
        # Mark executed
        await event_repo.mark_executed(db=test_db, event_id=test_event.id)
        
        # Verify
        updated = await event_repo.get_by_id(db=test_db, event_id=test_event.id)
        assert updated.executed is True

    @pytest.mark.asyncio
    async def test_update_cron_job_id(self, test_db, test_event):
        """Test that updating cron_job_id still works."""
        # Update
        result = await event_repo.update_cron_job_id(
            db=test_db,
            event_id=test_event.id,
            cron_job_id=88888
        )
        
        assert result is True
        
        # Verify
        updated = await event_repo.get_by_id(db=test_db, event_id=test_event.id)
        assert updated.cron_job_id == 88888


@pytest.mark.regression
class TestPhase1ADKSessionManager:
    """Regression tests for ADK session manager functionality."""

    @pytest.mark.asyncio
    async def test_session_manager_create_session(self, test_db, test_user):
        """Test that session creation still works."""
        manager = ADKSessionManager()
        
        session = await manager.get_or_create_session(
            app_name="regression_test",
            user_id=test_user.user_id,
            session_id="regression_session_test"
        )
        
        assert session is not None
        assert session.id == "regression_session_test"
        assert session.user_id == test_user.user_id

    @pytest.mark.asyncio
    async def test_session_manager_save_to_db(self, test_db, test_user):
        """Test that session persistence to DB still works."""
        manager = ADKSessionManager()
        session_id = "regression_session_save"
        
        state = {
            "user_id": test_user.user_id,
            "date": "2026-02-04",
            "test_key": "test_value"
        }
        
        result = await manager.save_agent_session_to_db(
            session_id=session_id,
            state=state,
            user_id=test_user.user_id
        )
        
        assert result is True

    @pytest.mark.asyncio
    async def test_session_manager_restore_from_db(self, test_db, test_user, test_session):
        """Test that session restoration from DB still works."""
        manager = ADKSessionManager()
        
        restored = await manager.restore_session_from_db(
            app_name="regression_test",
            session_id=test_session.session_id,
            user_id=test_user.user_id
        )
        
        assert restored is not None
        assert restored.id == test_session.session_id

    @pytest.mark.asyncio
    async def test_daily_session_id_generation(self):
        """Test that session ID generation still works."""
        with freeze_time("2026-02-04"):
            session_id = ADKSessionManager.get_daily_session_id("regression_user")
            
            assert session_id == "session_regression_user_2026-02-04"


@pytest.mark.regression
class TestPhase1Models:
    """Regression tests for SQLAlchemy models."""

    @pytest.mark.asyncio
    async def test_user_profile_model(self, test_db):
        """Test that UserProfile model still works."""
        from models import UserProfile
        
        user = UserProfile(
            user_id="model_test_user",
            wake_time="08:00",
            bedtime="22:00",
            timezone="UTC",
            health_anchors=["sleep"]
        )
        
        test_db.add(user)
        await test_db.commit()
        await test_db.refresh(user)
        
        assert user.user_id == "model_test_user"
        assert user.created_at is not None

    @pytest.mark.asyncio
    async def test_agent_session_model(self, test_db, test_user):
        """Test that AgentSession model still works."""
        from models import AgentSession
        
        session = AgentSession(
            session_id="model_test_session",
            user_id=test_user.user_id,
            date="2026-02-04",
            state={"test": "data"}
        )
        
        test_db.add(session)
        await test_db.commit()
        await test_db.refresh(session)
        
        assert session.session_id == "model_test_session"
        assert session.state["test"] == "data"

    @pytest.mark.asyncio
    async def test_scheduled_event_model(self, test_db, test_user):
        """Test that ScheduledEvent model still works."""
        from models import ScheduledEvent
        from datetime import timedelta
        import uuid
        
        event = ScheduledEvent(
            id=str(uuid.uuid4()),
            user_id=test_user.user_id,
            scheduled_time=datetime.utcnow() + timedelta(hours=1),
            event_type="test_event",
            payload={"test": "data"},
            executed=False
        )
        
        test_db.add(event)
        await test_db.commit()
        await test_db.refresh(event)
        
        assert event.user_id == test_user.user_id
        assert event.executed is False
