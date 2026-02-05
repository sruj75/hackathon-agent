"""
Shared pytest fixtures for testing.

Provides:
- In-memory test database
- Mocked external APIs (Expo, Cron-jobs.org, Gemini)
- Test session manager
- Test agent runtime
"""
import asyncio
import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

# Set test environment
os.environ['ENVIRONMENT'] = 'test'
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'

from database import Base, get_db
from models import UserProfile, UserPushToken, AgentSession, ScheduledEvent
from session_manager import ADKSessionManager


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_db_engine():
    """Create an in-memory SQLite database engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    
    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    # Cleanup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def test_db(test_db_engine):
    """Create a test database session."""
    async_session = async_sessionmaker(
        test_db_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    
    # Patch SessionLocal in modules that use it
    with patch('database.SessionLocal', async_session), \
         patch('session_manager.SessionLocal', async_session), \
         patch('agent_runtime.SessionLocal', async_session, create=True), \
         patch('main.SessionLocal', async_session, create=True):
        async with async_session() as session:
            yield session


@pytest.fixture
async def test_user(test_db):
    """Create a test user profile."""
    from repos import user_repo
    
    user = await user_repo.create_profile(
        db=test_db,
        user_id="user_test",
        wake_time="08:00",
        bedtime="22:00",
        timezone="America/New_York",
        health_anchors=["sleep", "meals", "exercise"]
    )
    await test_db.commit()
    return user


@pytest.fixture
async def test_push_token(test_db, test_user):
    """Create a test push token."""
    from repos import user_repo
    
    token = "ExponentPushToken[xxxxxxxxxxxxxxxxxxxxxx]"
    await user_repo.save_push_token(
        db=test_db,
        user_id=test_user.user_id,
        token=token
    )
    await test_db.commit()
    return token


@pytest.fixture
async def test_session(test_db, test_user):
    """Create a test agent session."""
    from repos import session_repo
    
    session_id = f"session_{test_user.user_id}_2026-02-04"
    session = await session_repo.save_session(
        db=test_db,
        session_id=session_id,
        user_id=test_user.user_id,
        date="2026-02-04",
        state={
            "conversation": [],
            "current_mode": "PLANNING",
            "today_tasks": []
        }
    )
    await test_db.commit()
    return session


@pytest.fixture
async def test_event(test_db, test_user):
    """Create a test scheduled event."""
    from repos import event_repo
    
    event = await event_repo.create_event(
        db=test_db,
        user_id=test_user.user_id,
        scheduled_time=datetime.utcnow() + timedelta(minutes=30),
        event_type="checkin",
        payload={"reason": "test_checkin"}
    )
    await test_db.commit()
    return event


@pytest.fixture
def mock_httpx_client():
    """Mock httpx.AsyncClient for external API calls."""
    with patch('httpx.AsyncClient') as mock_client_class:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        
        mock_client_instance = MagicMock()
        mock_client_instance.post = AsyncMock(return_value=mock_response)
        mock_client_instance.get = AsyncMock(return_value=mock_response)
        mock_client_instance.put = AsyncMock(return_value=mock_response)
        mock_client_instance.delete = AsyncMock(return_value=mock_response)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock(return_value=None)
        
        mock_client_class.return_value = mock_client_instance
        yield mock_client_instance


@pytest.fixture
def mock_expo_api(mock_httpx_client):
    """Mock Expo Push API responses."""
    mock_httpx_client.post.return_value.json.return_value = {
        "data": [{"status": "ok", "id": "test-notification-id"}]
    }
    return mock_httpx_client


@pytest.fixture
def mock_cron_api(mock_httpx_client):
    """Mock cron-jobs.org API responses."""
    mock_httpx_client.put.return_value.json.return_value = {
        "jobId": 12345,
        "status": "OK"
    }
    mock_httpx_client.delete.return_value.json.return_value = {
        "status": "OK"
    }
    return mock_httpx_client


@pytest.fixture
def mock_gemini_api():
    """Mock Gemini API responses."""
    with patch('google.genai.types.GenerateContentResponse') as mock_response:
        mock_response.text = "Test agent response"
        mock_response.candidates = []
        yield mock_response


@pytest.fixture
def mock_session_manager():
    """Mock ADKSessionManager for testing."""
    manager = MagicMock(spec=ADKSessionManager)
    manager.service = MagicMock()
    manager.get_or_create_session = AsyncMock()
    manager.save_session_to_db = AsyncMock()
    manager.restore_session_from_db = AsyncMock()
    manager.get_daily_session_id = MagicMock(return_value="session_user_test_2026-02-04")
    return manager


@pytest.fixture
def mock_agent_runtime():
    """Mock AgentRuntime for testing."""
    with patch('agent_runtime.AgentRuntime') as mock_runtime:
        mock_runtime.run_thinking_mode = AsyncMock()
        mock_runtime.get_conversation_mode_config = MagicMock()
        yield mock_runtime


@pytest.fixture
def freeze_time():
    """Freeze time for deterministic testing."""
    from freezegun import freeze_time as _freeze_time
    frozen_time = datetime(2026, 2, 4, 8, 0, 0)
    with _freeze_time(frozen_time):
        yield frozen_time


# Cleanup fixtures
@pytest.fixture(autouse=True)
async def cleanup_context_vars():
    """Reset context variables after each test."""
    from context import current_user_id, current_session_id, current_db
    
    yield
    
    # Reset context vars
    try:
        current_user_id.set(None)
    except:
        pass
    try:
        current_session_id.set(None)
    except:
        pass
    try:
        current_db.set(None)
    except:
        pass
