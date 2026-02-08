"""
Unit tests for database layer (repositories and models).

Tests user_repo, session_repo, and event_repo functionality.
"""
import pytest

pytest.skip(
    "Legacy SQLAlchemy-era tests skipped after Firestore migration.",
    allow_module_level=True,
)

from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession

from repos import user_repo, session_repo, event_repo
from models import UserProfile, UserPushToken, AgentSession, ScheduledEvent


class TestUserRepository:
    """Test suite for user_repo."""

    @pytest.mark.asyncio
    async def test_create_user_profile(self, test_db: AsyncSession):
        """Test creating a new user profile."""
        profile = await user_repo.create_profile(
            db=test_db,
            user_id="user_create_test",
            wake_time="07:00",
            bedtime="23:00",
            timezone="America/Los_Angeles",
            health_anchors=["sleep", "meals"]
        )
        
        assert profile is not None
        assert profile.user_id == "user_create_test"
        assert profile.wake_time == "07:00"
        assert profile.bedtime == "23:00"
        assert profile.timezone == "America/Los_Angeles"
        assert profile.health_anchors == ["sleep", "meals"]

    @pytest.mark.asyncio
    async def test_get_user_profile(self, test_db: AsyncSession, test_user: UserProfile):
        """Test retrieving an existing user profile."""
        profile = await user_repo.get_profile(db=test_db, user_id=test_user.user_id)
        
        assert profile is not None
        assert profile.user_id == test_user.user_id
        assert profile.wake_time == test_user.wake_time
        assert profile.bedtime == test_user.bedtime

    @pytest.mark.asyncio
    async def test_get_nonexistent_user_profile(self, test_db: AsyncSession):
        """Test retrieving a user that doesn't exist returns None."""
        profile = await user_repo.get_profile(db=test_db, user_id="nonexistent_user")
        
        assert profile is None

    @pytest.mark.asyncio
    async def test_update_user_profile(self, test_db: AsyncSession, test_user: UserProfile):
        """Test updating user preferences."""
        updated = await user_repo.update_profile(
            db=test_db,
            user_id=test_user.user_id,
            wake_time="06:30",
            timezone="America/Chicago"
        )
        
        assert updated.wake_time == "06:30"
        assert updated.timezone == "America/Chicago"
        assert updated.bedtime == test_user.bedtime  # Unchanged

    @pytest.mark.asyncio
    async def test_update_creates_profile_if_not_exists(self, test_db: AsyncSession):
        """Test update_profile creates a new profile if it doesn't exist."""
        profile = await user_repo.update_profile(
            db=test_db,
            user_id="new_user_via_update",
            wake_time="08:00",
            bedtime="22:00"
        )
        
        assert profile is not None
        assert profile.user_id == "new_user_via_update"
        assert profile.wake_time == "08:00"

    @pytest.mark.asyncio
    async def test_save_push_token(self, test_db: AsyncSession, test_user: UserProfile):
        """Test saving a push token."""
        token = "ExponentPushToken[test123abc]"
        token_obj = await user_repo.save_push_token(
            db=test_db,
            user_id=test_user.user_id,
            token=token
        )
        
        assert token_obj is not None
        assert token_obj.user_id == test_user.user_id
        assert token_obj.expo_push_token == token

    @pytest.mark.asyncio
    async def test_get_push_token(self, test_db: AsyncSession, test_user: UserProfile, test_push_token: str):
        """Test retrieving a push token."""
        token = await user_repo.get_push_token(db=test_db, user_id=test_user.user_id)
        
        assert token == test_push_token

    @pytest.mark.asyncio
    async def test_get_push_token_nonexistent(self, test_db: AsyncSession):
        """Test retrieving push token for user without one returns None."""
        token = await user_repo.get_push_token(db=test_db, user_id="user_no_token")
        
        assert token is None

    @pytest.mark.asyncio
    async def test_save_push_token_upsert(self, test_db: AsyncSession, test_user: UserProfile, test_push_token: str):
        """Test that saving a new token updates the existing one."""
        new_token = "ExponentPushToken[newtoken456]"
        token_obj = await user_repo.save_push_token(
            db=test_db,
            user_id=test_user.user_id,
            token=new_token
        )
        
        assert token_obj.expo_push_token == new_token
        
        # Verify only one token exists for this user
        retrieved = await user_repo.get_push_token(db=test_db, user_id=test_user.user_id)
        assert retrieved == new_token

    @pytest.mark.asyncio
    async def test_delete_push_token(self, test_db: AsyncSession, test_user: UserProfile, test_push_token: str):
        """Test deleting a push token."""
        await user_repo.delete_push_token(db=test_db, user_id=test_user.user_id)
        
        # Verify token is gone
        token = await user_repo.get_push_token(db=test_db, user_id=test_user.user_id)
        assert token is None

    @pytest.mark.asyncio
    async def test_get_all_users(self, test_db: AsyncSession):
        """Test retrieving all users."""
        # Create multiple users
        await user_repo.create_profile(test_db, "user1", "08:00", "22:00")
        await user_repo.create_profile(test_db, "user2", "07:00", "23:00")
        await user_repo.create_profile(test_db, "user3", "09:00", "21:00")
        
        users = await user_repo.get_all_users(db=test_db)
        
        assert len(users) >= 3
        user_ids = [u.user_id for u in users]
        assert "user1" in user_ids
        assert "user2" in user_ids
        assert "user3" in user_ids


class TestSessionRepository:
    """Test suite for session_repo."""

    @pytest.mark.asyncio
    async def test_save_session_new(self, test_db: AsyncSession, test_user: UserProfile):
        """Test saving a new agent session."""
        session_id = f"session_{test_user.user_id}_2026-02-04"
        state = {
            "user_id": test_user.user_id,
            "date": "2026-02-04",
            "conversation": [],
            "current_mode": "PLANNING"
        }
        
        session = await session_repo.save_session(
            db=test_db,
            session_id=session_id,
            state=state
        )
        
        assert session is not None
        assert session.session_id == session_id
        assert session.user_id == test_user.user_id
        assert session.date == "2026-02-04"
        assert session.state["current_mode"] == "PLANNING"

    @pytest.mark.asyncio
    async def test_save_session_update(self, test_db: AsyncSession, test_session: AgentSession):
        """Test updating an existing session."""
        updated_state = {
            "user_id": test_session.user_id,
            "date": test_session.date,
            "conversation": [{"role": "user", "message": "test"}],
            "current_mode": "EXECUTING"
        }
        
        session = await session_repo.save_session(
            db=test_db,
            session_id=test_session.session_id,
            state=updated_state
        )
        
        assert session.session_id == test_session.session_id
        assert session.state["current_mode"] == "EXECUTING"
        assert len(session.state["conversation"]) == 1

    @pytest.mark.asyncio
    async def test_get_session(self, test_db: AsyncSession, test_session: AgentSession):
        """Test retrieving a session by ID."""
        session = await session_repo.get_session(db=test_db, session_id=test_session.session_id)
        
        assert session is not None
        assert session.session_id == test_session.session_id
        assert session.user_id == test_session.user_id

    @pytest.mark.asyncio
    async def test_get_session_nonexistent(self, test_db: AsyncSession):
        """Test retrieving a session that doesn't exist returns None."""
        session = await session_repo.get_session(db=test_db, session_id="nonexistent_session")
        
        assert session is None

    @pytest.mark.asyncio
    async def test_get_today_session(self, test_db: AsyncSession, test_user: UserProfile, test_session: AgentSession):
        """Test retrieving a session for a specific date."""
        session = await session_repo.get_today_session(
            db=test_db,
            user_id=test_user.user_id,
            date="2026-02-04"
        )
        
        assert session is not None
        assert session.user_id == test_user.user_id
        assert session.date == "2026-02-04"

    @pytest.mark.asyncio
    async def test_session_state_json_serialization(self, test_db: AsyncSession, test_user: UserProfile):
        """Test that session state is properly serialized as JSON."""
        complex_state = {
            "user_id": test_user.user_id,
            "date": "2026-02-04",
            "conversation": [
                {"role": "user", "message": "Hello"},
                {"role": "agent", "message": "Hi there!"}
            ],
            "metadata": {
                "nested": {"key": "value"},
                "list": [1, 2, 3]
            }
        }
        
        session_id = f"session_{test_user.user_id}_complex"
        session = await session_repo.save_session(
            db=test_db,
            session_id=session_id,
            state=complex_state
        )
        
        # Retrieve and verify structure is preserved
        retrieved = await session_repo.get_session(db=test_db, session_id=session_id)
        assert retrieved.state["conversation"][0]["role"] == "user"
        assert retrieved.state["metadata"]["nested"]["key"] == "value"
        assert retrieved.state["metadata"]["list"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_upsert_session(self, test_db: AsyncSession, test_user: UserProfile):
        """Test explicit upsert_session function."""
        session_id = f"session_{test_user.user_id}_upsert"
        state = {"test": "data"}
        
        # Create
        session1 = await session_repo.upsert_session(
            db=test_db,
            session_id=session_id,
            user_id=test_user.user_id,
            date="2026-02-04",
            state=state
        )
        
        assert session1.session_id == session_id
        
        # Update
        state2 = {"test": "updated"}
        session2 = await session_repo.upsert_session(
            db=test_db,
            session_id=session_id,
            user_id=test_user.user_id,
            date="2026-02-04",
            state=state2
        )
        
        assert session2.session_id == session_id
        assert session2.state["test"] == "updated"


class TestEventRepository:
    """Test suite for event_repo."""

    @pytest.mark.asyncio
    async def test_create_event(self, test_db: AsyncSession, test_user: UserProfile):
        """Test creating a scheduled event."""
        scheduled_time = datetime.utcnow() + timedelta(hours=1)
        payload = {"reason": "morning_wake", "message": "Good morning!"}
        
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=scheduled_time,
            event_type="morning_wake",
            payload=payload
        )
        
        assert event is not None
        assert event.user_id == test_user.user_id
        assert event.event_type == "morning_wake"
        assert event.payload == payload
        assert event.executed is False
        assert event.cron_job_id is None

    @pytest.mark.asyncio
    async def test_create_event_with_cron_job_id(self, test_db: AsyncSession, test_user: UserProfile):
        """Test creating an event with a cron job ID."""
        scheduled_time = datetime.utcnow() + timedelta(minutes=30)
        
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=scheduled_time,
            event_type="checkin",
            payload={"reason": "deep_work_end"},
            cron_job_id=12345
        )
        
        assert event.cron_job_id == 12345

    @pytest.mark.asyncio
    async def test_update_cron_job_id(self, test_db: AsyncSession, test_event: ScheduledEvent):
        """Test updating the cron job ID for an event."""
        result = await event_repo.update_cron_job_id(
            db=test_db,
            event_id=test_event.id,
            cron_job_id=54321
        )
        
        assert result is True
        
        # Verify update
        event = await event_repo.get_by_id(db=test_db, event_id=test_event.id)
        assert event.cron_job_id == 54321

    @pytest.mark.asyncio
    async def test_update_cron_job_id_nonexistent(self, test_db: AsyncSession):
        """Test updating cron job ID for nonexistent event returns False."""
        result = await event_repo.update_cron_job_id(
            db=test_db,
            event_id="nonexistent_event",
            cron_job_id=99999
        )
        
        assert result is False

    @pytest.mark.asyncio
    async def test_mark_executed(self, test_db: AsyncSession, test_event: ScheduledEvent):
        """Test marking an event as executed."""
        assert test_event.executed is False
        
        await event_repo.mark_executed(db=test_db, event_id=test_event.id)
        
        # Verify
        event = await event_repo.get_by_id(db=test_db, event_id=test_event.id)
        assert event.executed is True

    @pytest.mark.asyncio
    async def test_get_by_id(self, test_db: AsyncSession, test_event: ScheduledEvent):
        """Test retrieving an event by ID."""
        event = await event_repo.get_by_id(db=test_db, event_id=test_event.id)
        
        assert event is not None
        assert event.id == test_event.id
        assert event.user_id == test_event.user_id

    @pytest.mark.asyncio
    async def test_get_by_id_nonexistent(self, test_db: AsyncSession):
        """Test retrieving nonexistent event returns None."""
        event = await event_repo.get_by_id(db=test_db, event_id="nonexistent_event")
        
        assert event is None

    @pytest.mark.asyncio
    async def test_get_event_by_type_and_time(self, test_db: AsyncSession, test_user: UserProfile):
        """Test finding an event by type and scheduled time."""
        scheduled_time = datetime(2026, 2, 5, 8, 0, 0)
        
        # Create event
        await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=scheduled_time,
            event_type="morning_wake",
            payload={}
        )
        
        # Find it
        event = await event_repo.get_event_by_type_and_time(
            db=test_db,
            user_id=test_user.user_id,
            event_type="morning_wake",
            scheduled_time=scheduled_time
        )
        
        assert event is not None
        assert event.event_type == "morning_wake"
        assert event.scheduled_time == scheduled_time

    @pytest.mark.asyncio
    async def test_event_lifecycle(self, test_db: AsyncSession, test_user: UserProfile):
        """Test complete event lifecycle: create → update cron ID → execute."""
        # Create
        scheduled_time = datetime.utcnow() + timedelta(hours=2)
        event = await event_repo.create_event(
            db=test_db,
            user_id=test_user.user_id,
            scheduled_time=scheduled_time,
            event_type="checkin",
            payload={"reason": "test_lifecycle"}
        )
        event_id = event.id
        
        assert event.executed is False
        assert event.cron_job_id is None
        
        # Update cron job ID
        await event_repo.update_cron_job_id(db=test_db, event_id=event_id, cron_job_id=777)
        
        event = await event_repo.get_by_id(db=test_db, event_id=event_id)
        assert event.cron_job_id == 777
        
        # Mark executed
        await event_repo.mark_executed(db=test_db, event_id=event_id)
        
        event = await event_repo.get_by_id(db=test_db, event_id=event_id)
        assert event.executed is True
