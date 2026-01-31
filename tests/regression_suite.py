import asyncio
import os
import sys
import uuid
import logging
import json
from datetime import datetime, timedelta, time, date
from typing import Dict, Any, List
from unittest.mock import MagicMock, AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("RegressionSuite")

# Add agent directory to sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_DIR = os.path.dirname(CURRENT_DIR)
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

# Import app components
try:
    from main import app, CRON_API_KEY, session_manager, schedule_morning_wakes
    from database import DATABASE_URL, Base, SessionLocal
    from models import UserProfile, UserPushToken, AgentSession, ScheduledEvent
    from repos import user_repo, session_repo, event_repo
    from context import current_user_id, current_session_id
    from voice_agent.get_time import get_current_time, get_user_preferences
    from voice_agent.set_timer import set_checkin_timer
    from agent_runtime import AgentRuntime
    from google.adk.agents.run_config import RunConfig, StreamingMode
except ImportError as e:
    logger.error(f"Import failed: {e}")
    sys.exit(1)

# --- Configuration ---
TEST_USER_ID = "unified_test_user"
# We don't set a static TEST_SESSION_ID anymore because logic dictates it!
TEST_DATABASE_URL = "sqlite+aiosqlite:///test_regression.db"

# --- Test Summary ---
results = {
    "passed": 0,
    "failed": 0,
    "errors": []
}

def report_pass(name: str):
    results["passed"] += 1
    logger.info(f"✅ PASS: {name}")

def report_fail(name: str, error: str):
    results["failed"] += 1
    results["errors"].append({"test": name, "error": error})
    logger.error(f"❌ FAIL: {name} - {error}")

# --- Mocks ---
import notification_service
import voice_agent.composio_tools

# Mock Composio timezone fetch to avoid network calls
voice_agent.composio_tools._get_user_timezone = lambda: "America/New_York"

# Mock send_push_notification to avoid network calls
async def mock_send_push_notification(user_id, title, body, data):
    logger.info(f"🚀 [MOCKED PUSH] To: {user_id}, Title: {title}, Body: {body}")
    return True

notification_service.send_push_notification = mock_send_push_notification

# --- Test Suite ---

async def test_unified_session_id_logic():
    """Verifies deterministic session ID generation."""
    name = "Unit: Unified Session ID"
    try:
        user_id = "test_user_123"
        today_iso = datetime.now().date().isoformat()
        expected = f"session_{user_id}_{today_iso}"
        
        generated = session_manager.get_daily_session_id(user_id)
        
        assert generated == expected, f"Expected {expected}, got {generated}"
        report_pass(name)
    except Exception as e:
        report_fail(name, str(e))

async def test_agent_runtime_config():
    """Verifies Conversation Mode config (Audio)."""
    name = "Unit: Agent Runtime Config"
    try:
        config = AgentRuntime.get_conversation_mode_config()
        assert "AUDIO" in config.response_modalities, "Conversation mode must include AUDIO"
        assert config.streaming_mode == StreamingMode.BIDI, "Conversation mode must be BIDI"
        report_pass(name)
    except Exception as e:
        report_fail(name, str(e))

async def test_db_persistence_thinking_mode():
    """Integration: Thinking Mode Execution & Persistence."""
    name = "Integration: Thinking Mode Persistence"
    try:
        # Mock the Runner to avoid real LLM calls
        mock_runner_instance = MagicMock()
        mock_runner_instance.run_async = MagicMock()
        
        # We need to simulate the async generator of run_async
        async def async_gen(*args, **kwargs):
            yield MagicMock(content=MagicMock(parts=[MagicMock(text="I am thinking")]))
        
        mock_runner_instance.run_async.side_effect = async_gen

        # Patch Runner in agent_runtime
        with patch('agent_runtime.Runner', return_value=mock_runner_instance):
             with patch('agent_runtime.Agent'): # Patch Agent creation too
                
                # Run Thinking Mode
                trigger = "Wake up!"
                
                # We need to collect the generator
                events = []
                async for event in AgentRuntime.run_thinking_mode(TEST_USER_ID, trigger, session_manager):
                    events.append(event)
                
                assert len(events) > 0, "Thinking mode produced no events"
                
                # VERIFY PERSISTENCE
                # 1. Check generated Session ID
                today_iso = datetime.now().date().isoformat()
                expected_sid = f"session_{TEST_USER_ID}_{today_iso}"
                
                # 2. Check DB for this session
                async with SessionLocal() as db:
                    session = await session_repo.get_session(db, expected_sid)
                    assert session is not None, "Session was not persisted to DB"
                    assert session.user_id == TEST_USER_ID
                    # Verify state was saved (mock run_async doesn't update state directly in this test harness 
                    # unless we mock session_service too, but at least the row exists)
                    
        report_pass(name)
    except Exception as e:
        report_fail(name, str(e))

async def test_push_token_api():
    """Verifies POST /api/save-token endpoint."""
    name = "Integration: Push Token API"
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # 1. Save Token
            payload = {"user_id": TEST_USER_ID, "token": "ExponentPushToken[unified_test]"}
            resp = await client.post("/api/save-token", json=payload)
            assert resp.status_code == 200, f"API failed: {resp.text}"
            
            # 2. Verify in DB
            async with SessionLocal() as db:
                token = await user_repo.get_push_token(db, TEST_USER_ID)
                assert token == "ExponentPushToken[unified_test]", "Token not saved correctly"
            
        report_pass(name)
    except Exception as e:
        report_fail(name, str(e))

async def test_full_event_flow_with_runtime():
    """Integration: Event -> API -> AgentRuntime -> DB."""
    name = "Integration: Unified Event Flow"
    try:
        # Schedule an event
        # Mock AgentRuntime.run_thinking_mode to avoid real AI call
        
        async def mock_thinking_gen(user_id, trigger, sm):
             yield MagicMock(content=MagicMock(parts=[MagicMock(text="Processed")]))
        
        with patch.object(AgentRuntime, 'run_thinking_mode', side_effect=mock_thinking_gen) as mock_run:
            
            # 1. Create Event
            async with SessionLocal() as db:
                event = await event_repo.create_event(
                    db, TEST_USER_ID, datetime.now(), "checkin", {"msg": "Unified Test"}
                )
                event_id = event.id
            
            # 2. Execute via API
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.post(f"/api/execute-event/{event_id}", headers={"x-cron-secret": CRON_API_KEY})
                assert resp.status_code == 200
                assert resp.json()["status"] == "executed"
            
            # 3. Verify AgentRuntime was called
            assert mock_run.called, "AgentRuntime.run_thinking_mode was not called"
            args = mock_run.call_args
            assert args.kwargs['user_id'] == TEST_USER_ID
            
            # 4. Verify Event Marked Executed
            async with SessionLocal() as db:
                updated = await event_repo.get_by_id(db, event_id)
                assert updated.executed is True
                
        report_pass(name)
    except Exception as e:
        report_fail(name, str(e))

async def run_suite():
    logger.info("🚀 Starting Unified Architecture Regression Suite")
    
    # Init DB
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        pass # Create table logic if needed, but we assume app logic created them
        
    # Run Tests
    await test_unified_session_id_logic()
    await test_agent_runtime_config()
    await test_push_token_api()
    
    # These integration tests rely on patch/mock logic for the runtime
    await test_db_persistence_thinking_mode()
    await test_full_event_flow_with_runtime()
    
    # --- Final Report ---
    print("\n" + "="*50)
    print("      UNIFIED REGRESSION SUITE SUMMARY")
    print("="*50)
    print(f"PASSED: {results['passed']}")
    print(f"FAILED: {results['failed']}")
    
    if results["failed"] > 0:
        print("\nERRORS FOUND:")
        for err in results["errors"]:
            print(f"  - {err['test']}: {err['error']}")
        sys.exit(1)
    else:
        print("\n✅ UNIFIED ARCHITECTURE VERIFIED")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(run_suite())
