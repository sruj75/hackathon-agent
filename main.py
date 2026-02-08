"""
FastAPI Backend for Intentive Voice Agent

This is the production FastAPI server that provides:
- WebSocket endpoint for real-time audio streaming with ADK
- Health check endpoint
"""
import asyncio
import json
import logging
import os
import re
import warnings
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables BEFORE importing agent
load_dotenv(Path(__file__).parent / ".env")

# Add the current directory (agent/) to sys.path so that absolute imports work correctly
import sys
sys.path.insert(0, str(Path(__file__).parent))

# Import agent after loading env
from voice_agent.agent import (  # noqa: E402
    conversation_agent as agent,
)
from voice_agent.render_ui_tools import set_ui_event_queue, get_ui_event_queue  # noqa: E402

from google.adk.runners import Runner
from session_manager import ADKSessionManager
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.genai import types
from context import current_session_id, current_user_id, current_user_timezone

# New imports for Cron Endpoints
from fastapi import HTTPException
from repos import event_repo, user_repo
from datetime import datetime, timedelta, time, timezone
from agent_runtime import AgentRuntime
import cron_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

HHMM_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
SESSION_ID_PATTERN = re.compile(r"^session_(?P<user_id>.+)_(?P<date>\d{4}-\d{2}-\d{2})$")


def _event_field(event: object, field: str, default=None):
    """Read an event field from either a dict record or object-style record."""
    if isinstance(event, dict):
        return event.get(field, default)
    return getattr(event, field, default)

# Suppress Pydantic serialization warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Application name constant
APP_NAME = "intentive-coach"

# ========================================
# FastAPI App Setup
# ========================================

app = FastAPI(
    title="Intentive Voice Agent API",
    description="Real-time voice AI coaching with Gemini Live API",
    version="1.0.0",
)

# CORS middleware for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session and Runner setup
# Session and Runner setup
session_manager = ADKSessionManager()
runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_manager.service)


def _is_live_transient_error(error: Exception) -> bool:
    """Detect transient Live API availability/overload errors."""
    message = str(error).lower()
    return (
        "service is currently unavailable" in message
        or " 503 " in message
        or "unavailable" in message
        or "overloaded" in message
    )


def _is_valid_hhmm(value: str | None) -> bool:
    return bool(value and HHMM_PATTERN.match(value))


def _parse_wake_time(wake_time_raw: str | None) -> tuple[int, int] | None:
    """Parse HH:MM wake-time strings. Returns None when missing/invalid."""
    if not wake_time_raw:
        return None
    try:
        hour_str, minute_str = wake_time_raw.strip().split(":", 1)
        hour = int(hour_str)
        minute = int(minute_str)
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        return (hour, minute)
    except Exception:
        return None


def _validate_profile_for_scheduler(profile: dict | None) -> tuple[bool, list[str]]:
    """Validate required profile fields before creating scheduled wake events."""
    profile = profile or {}
    errors: list[str] = []

    if not profile.get("user_id"):
        errors.append("missing_user_id")

    wake_time = profile.get("wake_time")
    if not _is_valid_hhmm(wake_time):
        errors.append("invalid_wake_time")

    timezone_name = profile.get("timezone")
    if not timezone_name:
        errors.append("missing_timezone")
    else:
        try:
            ZoneInfo(timezone_name)
        except Exception:
            errors.append("invalid_timezone")

    return (len(errors) == 0, errors)


def _normalize_health_anchors(value: object) -> list[str]:
    """Normalize health anchors to a clean string list."""
    if not isinstance(value, list):
        return []
    return [
        anchor.strip()
        for anchor in value
        if isinstance(anchor, str) and anchor.strip()
    ]


def _validate_preferences_payload(payload: dict) -> tuple[dict, list[str]]:
    """Validate and sanitize incoming user preference payload."""
    errors: list[str] = []

    wake_time = payload.get("wake_time")
    bedtime = payload.get("bedtime")
    timezone_name = payload.get("timezone")
    has_health_anchors = "health_anchors" in payload
    health_anchors = _normalize_health_anchors(payload.get("health_anchors"))

    if not _is_valid_hhmm(wake_time):
        errors.append("wake_time must be HH:MM in 24-hour format")
    if not _is_valid_hhmm(bedtime):
        errors.append("bedtime must be HH:MM in 24-hour format")

    if not timezone_name or not isinstance(timezone_name, str):
        errors.append("timezone is required")
    else:
        try:
            ZoneInfo(timezone_name)
        except Exception:
            errors.append("timezone must be a valid IANA timezone")

    sanitized: dict[str, object] = {
        "wake_time": wake_time,
        "bedtime": bedtime,
        "timezone": timezone_name,
    }
    # Preserve existing anchors unless explicitly provided by caller.
    if has_health_anchors:
        sanitized["health_anchors"] = health_anchors
    return sanitized, errors


def _profile_is_complete(profile: dict | None) -> bool:
    if not profile:
        return False
    valid_scheduler_profile, _ = _validate_profile_for_scheduler(profile)
    return valid_scheduler_profile


def _select_ws_session_id(user_id: str, requested_session_id: str | None) -> str:
    """
    Select conversation session ID.

    Uses explicit session IDs only when they follow our deterministic pattern and
    belong to the same user; otherwise falls back to today's unified session.
    """
    fallback_session_id = ADKSessionManager.get_daily_session_id(user_id)
    if not requested_session_id:
        return fallback_session_id

    match = SESSION_ID_PATTERN.match(requested_session_id)
    if not match:
        return fallback_session_id

    if match.group("user_id") != user_id:
        return fallback_session_id

    return requested_session_id


def _normalize_timezone(timezone_name: str | None, fallback: str = "UTC") -> str:
    """Return a valid IANA timezone or fallback."""
    if not timezone_name:
        return fallback
    try:
        ZoneInfo(timezone_name)
        return timezone_name
    except Exception:
        logger.warning(f"Invalid timezone '{timezone_name}'. Falling back to {fallback}.")
        return fallback


def _next_morning_wake_datetime(
    user_timezone: str, wake_time_raw: str | None
) -> datetime | None:
    """
    Compute next local morning wake datetime for a user.
    If today's wake time already passed, schedule for tomorrow.
    """
    tz = ZoneInfo(user_timezone or "UTC")
    now_local = datetime.now(tz)
    parsed_wake_time = _parse_wake_time(wake_time_raw)
    if not parsed_wake_time:
        return None
    wake_hour, wake_minute = parsed_wake_time
    target_local = now_local.replace(
        hour=wake_hour, minute=wake_minute, second=0, microsecond=0
    )
    if target_local <= now_local:
        target_local = target_local + timedelta(days=1)
    return target_local


def _as_datetime(value) -> datetime | None:
    """Best-effort conversion for Firestore datetime fields."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return None


async def _ensure_morning_wake_for_user(user: dict) -> None:
    """
    Ensure exactly one pending morning wake event exists for user's next wake date.
    Creates event + cron if missing; backfills/reschedules cron for existing events.
    """
    is_valid, validation_errors = _validate_profile_for_scheduler(user)
    if not is_valid:
        logger.warning(
            f"[morning-bootstrap] Skipping morning wake due to invalid profile "
            f"user_id={(user or {}).get('user_id')}: errors={validation_errors}"
        )
        return

    user_id = str(user.get("user_id"))
    user_timezone = str(user.get("timezone"))
    wake_time_raw = str(user.get("wake_time"))
    target_local = _next_morning_wake_datetime(user_timezone, wake_time_raw)
    if not target_local:
        logger.warning(
            f"[morning-bootstrap] Skipping morning wake for user {user_id}: "
            f"missing/invalid wake_time='{wake_time_raw}'"
        )
        return
    seed_date = target_local.date().isoformat()

    existing = await event_repo.find_pending_morning_event(user_id, seed_date)
    if existing:
        event_id = _event_field(existing, "id")
        if not event_id:
            logger.warning(
                f"[morning-bootstrap] Existing event missing id for user {user_id}, seed_date={seed_date}"
            )
            return

        existing_scheduled = _as_datetime(_event_field(existing, "scheduled_time"))
        existing_payload = _event_field(existing, "payload", {}) or {}
        existing_cron_job_id = _event_field(existing, "cron_job_id")

        # Reschedule when wake time/timezone changed, or when cron id is missing.
        should_reschedule = (
            not existing_cron_job_id
            or not existing_scheduled
            or abs((existing_scheduled - target_local).total_seconds()) >= 60
            or _normalize_timezone(existing_payload.get("timezone"), "") != user_timezone
        )

        if should_reschedule:
            if existing_cron_job_id:
                try:
                    await cron_service.delete_job(existing_cron_job_id)
                except Exception as cleanup_error:
                    logger.warning(
                        f"[morning-bootstrap] Failed to cleanup previous cron job {existing_cron_job_id}: {cleanup_error}"
                    )

            cron_job_id = await cron_service.create_one_time_job(
                target_datetime=target_local,
                event_id=event_id,
                timezone=user_timezone,
            )
            await event_repo.update_event(
                event_id,
                scheduled_time=target_local,
                payload={
                    "reason": "daily_bootstrap",
                    "seed_date": seed_date,
                    "timezone": user_timezone,
                },
                executed=False,
                cron_job_id=cron_job_id,
            )
            logger.info(
                f"[morning-bootstrap] Upserted cron for morning event user={user_id}, "
                f"event_id={event_id}, wake={target_local.isoformat()}, cron_job_id={cron_job_id}"
            )
        return

    created = await event_repo.create_event(
        user_id=user_id,
        scheduled_time=target_local,
        event_type="morning_wake",
        payload={
            "reason": "daily_bootstrap",
            "seed_date": seed_date,
            "timezone": user_timezone,
        },
    )
    event_id = _event_field(created, "id")
    if not event_id:
        raise ValueError(f"[morning-bootstrap] Failed to create event id for user {user_id}")

    cron_job_id = await cron_service.create_one_time_job(
        target_datetime=target_local,
        event_id=event_id,
        timezone=user_timezone,
    )
    await event_repo.update_cron_job_id(event_id, cron_job_id)
    logger.info(
        f"[morning-bootstrap] Created morning wake event user={user_id}, "
        f"event_id={event_id}, wake={target_local.isoformat()}, cron_job_id={cron_job_id}"
    )


async def _reconcile_missing_cron_jobs() -> None:
    """
    Backfill cron jobs for future events that were created but failed to schedule.
    """
    missing_events = await event_repo.list_future_unexecuted_events_missing_cron(limit=200)
    if not missing_events:
        return

    logger.info(f"[cron-reconcile] Found {len(missing_events)} events missing cron_job_id")
    for event in missing_events:
        try:
            event_id = _event_field(event, "id")
            if not event_id:
                continue
            scheduled_time = _as_datetime(_event_field(event, "scheduled_time"))
            if not scheduled_time:
                continue

            payload = _event_field(event, "payload", {}) or {}
            timezone_name = payload.get("timezone")
            if not timezone_name:
                profile = await user_repo.get_profile(_event_field(event, "user_id", ""))
                timezone_name = (profile or {}).get("timezone", "UTC")
            timezone_name = _normalize_timezone(timezone_name, "UTC")

            cron_job_id = await cron_service.create_one_time_job(
                target_datetime=scheduled_time,
                event_id=event_id,
                timezone=timezone_name,
            )
            await event_repo.update_cron_job_id(event_id, cron_job_id)
            logger.info(
                f"[cron-reconcile] Backfilled cron job for event {event_id}: {cron_job_id}"
            )
        except Exception as e:
            logger.warning(f"[cron-reconcile] Failed for event {_event_field(event, 'id')}: {e}")


# ========================================
# Endpoints
# ========================================

@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "app": APP_NAME, "agent": agent.name}


@app.get("/health")
async def health():
    """Health check for deployment monitoring."""
    return {"status": "healthy"}


# ========================================
# User Preferences Endpoints
# ========================================

@app.get("/api/preferences/{user_id}")
async def get_user_preferences(user_id: str):
    """Read persisted user preferences."""
    profile = await user_repo.get_profile(user_id)
    if not profile:
        return {
            "status": "not_found",
            "user_id": user_id,
            "preferences": None,
            "is_complete": False,
        }

    preferences = {
        "wake_time": profile.get("wake_time"),
        "bedtime": profile.get("bedtime"),
        "timezone": profile.get("timezone"),
        "health_anchors": _normalize_health_anchors(profile.get("health_anchors")),
    }
    return {
        "status": "ok",
        "user_id": user_id,
        "preferences": preferences,
        "is_complete": _profile_is_complete(profile),
    }


@app.put("/api/preferences/{user_id}")
async def update_user_preferences(user_id: str, payload: dict):
    """Create/update user preferences and re-sync morning wake scheduling."""
    sanitized, errors = _validate_preferences_payload(payload)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    updated_profile = await user_repo.update_profile(user_id, **sanitized)

    scheduler_error = None
    try:
        await _ensure_morning_wake_for_user(updated_profile)
    except Exception as e:
        scheduler_error = str(e)
        logger.warning(
            f"[preferences] Failed to resync morning wake for user {user_id}: {e}"
        )

    return {
        "status": "ok" if scheduler_error is None else "partial_success",
        "user_id": user_id,
        "preferences": {
            "wake_time": updated_profile.get("wake_time"),
            "bedtime": updated_profile.get("bedtime"),
            "timezone": updated_profile.get("timezone"),
            "health_anchors": _normalize_health_anchors(
                updated_profile.get("health_anchors")
            ),
        },
        "scheduler": {
            "resynced": scheduler_error is None,
            "error": scheduler_error,
        },
    }


# ========================================
# Cron Endpoints
# ========================================

@app.post("/api/execute-event/{event_id}")
async def execute_event(
    event_id: str
):
    """
    Execute a specific scheduled event.
    Called by cron-jobs.org at the scheduled time.
    
    Security: Event IDs are UUIDs (unguessable) and execution is idempotent.
    """
    event = await event_repo.get_by_id(event_id)
    
    # Null check: if event doesn't exist, return 404
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Idempotency check: if already executed, return success
    if _event_field(event, "executed", False):
        logger.info(f"Event {event_id} already executed, skipping")
        return {"status": "already_executed"}
    
    # Agent Logic (Hybrid Architecture)
    # UNIFIED ARCHITECTURE: Thinking Mode (Standard API) via AgentRuntime
    
# ========================================
# Agent Logic: Thinking Mode (Text)
# ========================================
    
    # 1. Trigger Prompt (minimal but event-aware)
    event_type = _event_field(event, "event_type", "checkin")
    payload = _event_field(event, "payload", {}) or {}
    reason = payload.get("reason")
    event_timezone = _normalize_timezone(payload.get("timezone"), fallback="")
    if not event_timezone:
        try:
            profile = await user_repo.get_profile(_event_field(event, "user_id", ""))
        except Exception as profile_error:
            logger.warning(
                f"[execute-event] Failed to load profile timezone for {_event_field(event, 'user_id', '')}: {profile_error}"
            )
            profile = None
        event_timezone = _normalize_timezone((profile or {}).get("timezone"), "UTC")

    if event_type == "morning_wake":
        trigger_prompt = "You just woke up."
    else:
        trigger_prompt = (
            f"A scheduled check-in timer fired. reason={reason or 'unspecified'}"
        )
    
    # 2. Run Turn via AgentRuntime
    event_user_id = _event_field(event, "user_id")
    if not event_user_id:
        raise HTTPException(status_code=500, detail="Event missing user_id")

    logger.info(f"--- Calling AgentRuntime.run_thinking_mode for user {event_user_id} ---")
    try:
        async for agent_event in AgentRuntime.run_thinking_mode(
            user_id=event_user_id,
            trigger_context=trigger_prompt,
            session_manager=session_manager,
            timezone=event_timezone,
        ):
            # Log significant events
            if hasattr(agent_event, "content") and agent_event.content and agent_event.content.parts:
                for part in agent_event.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                         logger.info(f"🤖 [THINKING] Tool Call: {part.function_call.name}")
                    if hasattr(part, "text") and part.text:
                         logger.info(f"🤖 [THINKING] Agent response: {part.text}")
        
    except Exception as e:
        logger.error(f"❌ [THINKING] Agent failed to run: {e}")
        retry_delay_minutes = int(os.getenv("EXECUTE_EVENT_RETRY_DELAY_MINUTES", "2"))
        max_retries = int(os.getenv("EXECUTE_EVENT_MAX_RETRIES", "2"))
        now_utc = datetime.now(timezone.utc)
        retry_count = 0
        try:
            retry_count = int(payload.get("retry_count", 0))
        except Exception:
            retry_count = 0

        if retry_count >= max_retries:
            await event_repo.update_event(
                event_id,
                executed=True,
                last_error=str(e),
                last_attempt_at=now_utc,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "status": "failed_permanently",
                    "event_id": event_id,
                    "retry_count": retry_count,
                    "error": str(e),
                },
            )

        retry_at = now_utc + timedelta(minutes=retry_delay_minutes)
        next_retry_count = retry_count + 1
        retry_payload = {
            **payload,
            "retry_count": next_retry_count,
        }

        try:
            retry_cron_job_id = await cron_service.create_one_time_job(
                target_datetime=retry_at,
                event_id=event_id,
                timezone=event_timezone,
            )
            await event_repo.update_event(
                event_id,
                scheduled_time=retry_at,
                payload=retry_payload,
                executed=False,
                cron_job_id=retry_cron_job_id,
                last_error=str(e),
                last_attempt_at=now_utc,
            )
            return {
                "status": "retry_scheduled",
                "event_id": event_id,
                "retry_count": next_retry_count,
                "retry_at": retry_at.isoformat(),
            }
        except Exception as retry_error:
            await event_repo.update_event(
                event_id,
                executed=False,
                last_error=f"agent_error={e}; retry_error={retry_error}",
                last_attempt_at=now_utc,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "status": "agent_failed",
                    "event_id": event_id,
                    "retry_scheduled": False,
                    "error": str(e),
                },
            )
    
    # Mark event as executed
    await event_repo.mark_executed(event_id)
    
    # Cleanup: Delete the cron job from cron-jobs.org
    event_cron_job_id = _event_field(event, "cron_job_id")
    if event_cron_job_id:
        try:
            await cron_service.delete_job(event_cron_job_id)
        except Exception as cleanup_error:
            logger.warning(f"Failed to cleanup cron job {event_cron_job_id}: {cleanup_error}")
            # Don't fail the request if cleanup fails
    
    return {"status": "executed", "agent_response": "processed"}

@app.post("/api/save-token")
async def save_push_token(
    payload: dict
):
    """
    Saves the user's Expo push token.
    Payload expected: {"user_id": "...", "token": "..."}
    """
    import re
    
    user_id = payload.get("user_id")
    token = payload.get("token")
    
    if not user_id or not token:
        raise HTTPException(status_code=400, detail="Missing user_id or token")
    
    # Validate Expo token format
    if not re.match(r'^ExponentPushToken\[.+\]$', token):
        raise HTTPException(status_code=400, detail="Invalid token format")
        
    await user_repo.save_push_token(user_id, token)
    return {"status": "saved", "user_id": user_id}


@app.on_event("startup")
async def reliability_bootstrap() -> None:
    """
    Reliability bootstrap:
    1) Ensure each user has a pending next-morning wake event + cron
    2) Reconcile future events that are missing cron_job_id
    """
    if os.getenv("ENABLE_RELIABILITY_BOOTSTRAP", "true").lower() not in (
        "1",
        "true",
        "yes",
    ):
        logger.info("[bootstrap] Reliability bootstrap disabled by env")
        return

    try:
        users = await user_repo.get_all_users()
        logger.info(f"[bootstrap] Seeding morning wake events for {len(users)} users")
        for user in users:
            try:
                await _ensure_morning_wake_for_user(user)
            except Exception as user_error:
                logger.warning(
                    f"[bootstrap] Failed to seed morning wake for user {(user or {}).get('user_id')}: {user_error}"
                )
    except Exception as e:
        logger.warning(f"[bootstrap] Failed to seed morning wakes: {e}")

    try:
        await _reconcile_missing_cron_jobs()
    except Exception as e:
        logger.warning(f"[bootstrap] Failed cron reconciliation: {e}")


@app.websocket("/ws/{user_id}/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
) -> None:
    """
    WebSocket endpoint for bidirectional streaming with ADK.
    
    Args:
        websocket: The WebSocket connection
        user_id: User identifier
        session_id: Session identifier
    """
    logger.info(f"WebSocket connection request: user_id={user_id}, session_id={session_id}")
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    # Override session_id with the deterministic daily ID
    # This aligns the WebSocket connection with the same session used by cron/background agent.
    # We ignore the client-provided session_id (which is often random or stale).
    unified_session_id = _select_ws_session_id(user_id, session_id)
    logger.info(f"Map WebSocket connection to Unified Session ID: {unified_session_id}")

    # Set context variables for this request/connection
    current_user_id.set(user_id)
    current_session_id.set(unified_session_id)

    # ========================================
    # Session Initialization
    # ========================================
    
    # Determine response modality based on Conversation Mode (Live API)
    # Conversation Mode: Audio/Video
    run_config = AgentRuntime.get_conversation_mode_config()
    logger.info(f"Using Conversation Mode (AUDIO) for session: {unified_session_id}")

    # Get or create session
    session = await session_manager.get_or_create_session(
        app_name=APP_NAME, user_id=user_id, session_id=unified_session_id
    )
    try:
        profile = await user_repo.get_profile(user_id)
    except Exception as profile_error:
        logger.warning(f"[WS-INIT] Failed to load profile for {user_id}: {profile_error}")
        profile = None
    profile_timezone = _normalize_timezone((profile or {}).get("timezone"), "UTC")
    current_user_timezone.set(profile_timezone)
    session.state.setdefault("user_timezone", profile_timezone)

    live_request_queue = LiveRequestQueue()
    logger.info(f"WebSocket model: {agent.model}")
    
    # Create UI event queue for this connection
    ui_event_queue = asyncio.Queue()
    set_ui_event_queue(ui_event_queue)
    logger.info("UI event queue created for this connection")

    # Optional auto-activation text turn. Disabled by default because mixing
    # send_content and realtime audio at connection start can cause live API
    # argument errors depending on model/backend behavior.
    if os.getenv("WS_SEND_ACTIVATION_MESSAGE", "false").lower() in ("1", "true", "yes"):
        activation_message = types.Content(
            role="user",
            parts=[types.Part(text="Hello")]
        )
        live_request_queue.send_content(activation_message)
        logger.info("Sent activation message to start conversation")

    # ========================================
    # Bidirectional Streaming Tasks
    # ========================================

    async def upstream_task() -> None:
        """Receives messages from WebSocket and sends to LiveRequestQueue."""
        logger.debug("upstream_task started")
        init_handled = False
        
        try:
            while True:
                message = await websocket.receive()
                
                # Handle disconnect
                if message.get("type") == "websocket.disconnect":
                    logger.info("WebSocket disconnect received in upstream_task")
                    break

                # Handle binary frames (audio data)
                audio_data = message.get("bytes")
                if audio_data is not None:
                    logger.debug(f"Received audio chunk: {len(audio_data)} bytes")
                    audio_blob = types.Blob(
                        mime_type="audio/pcm;rate=16000", data=audio_data
                    )
                    live_request_queue.send_realtime(audio_blob)

                # Handle text frames (JSON messages)
                elif message.get("text") is not None:
                    text_data = message["text"]
                    logger.debug(f"Received text message: {text_data[:100]}...")
                    
                    try:
                        json_message = json.loads(text_data)
                        
                        # Handle init message (should be first message from client)
                        if json_message.get("type") == "init" and not init_handled:
                            init_handled = True
                            resume_session_id = json_message.get("resume_session_id")
                            trigger_type = json_message.get("trigger_type")
                            client_timezone = _normalize_timezone(
                                json_message.get("timezone"), profile_timezone
                            )
                            
                            logger.info(
                                "[WS-INIT] Received init handshake - "
                                f"resume_session_id: {resume_session_id}, "
                                f"trigger_type: {trigger_type}, "
                                f"timezone: {client_timezone}"
                            )
                            
                            # Store trigger_type in session state for agent context
                            if trigger_type:
                                session.state["trigger_type"] = trigger_type
                                logger.info(f"[WS-INIT] Stored trigger_type in session: {trigger_type}")

                            session.state["user_timezone"] = client_timezone
                            current_user_timezone.set(client_timezone)
                            try:
                                await user_repo.update_profile(user_id, timezone=client_timezone)
                                logger.info(
                                    f"[WS-INIT] Stored user timezone in session/profile: {client_timezone}"
                                )
                            except Exception as profile_update_error:
                                logger.warning(
                                    f"[WS-INIT] Failed to persist timezone for {user_id}: {profile_update_error}"
                                )
                            
                            if resume_session_id and resume_session_id != unified_session_id:
                                logger.info(
                                    f"[WS-INIT] Received resume_session_id={resume_session_id}, "
                                    f"using session_id={unified_session_id}"
                                )
                            
                            continue  # Don't process init message further
                        
                        if json_message.get("type") == "text":
                            content = types.Content(
                                parts=[types.Part(text=json_message["text"])]
                            )
                            live_request_queue.send_content(content)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON received: {text_data}")
        except Exception as e:
            logger.debug(f"upstream_task ended: {e}")

    async def _process_downstream_event(event) -> bool:
        """Process one ADK event and forward to frontend. Returns False on closed socket."""
        # Explicitly check and log transcriptions
        if hasattr(event, "server_content") and event.server_content:
            if (
                hasattr(event.server_content, "input_transcription")
                and event.server_content.input_transcription
            ):
                logger.info(
                    f"[TRANSCRIPTION-INPUT] User: {event.server_content.input_transcription.text}"
                )

            if (
                hasattr(event.server_content, "output_transcription")
                and event.server_content.output_transcription
            ):
                logger.info(
                    f"[TRANSCRIPTION-OUTPUT] Agent: {event.server_content.output_transcription.text}"
                )

        # Log every event with content
        if event.content and event.content.parts:
            for i, part in enumerate(event.content.parts):
                part_attrs = [
                    a
                    for a in ["text", "function_call", "function_response", "inline_data"]
                    if getattr(part, a, None) is not None
                ]
                if part_attrs:
                    logger.info(f"[MAIN-EVENT] Part {i} has: {part_attrs}")

                func_resp = getattr(part, "function_response", None)
                if func_resp is not None:
                    func_name = getattr(func_resp, "name", "unknown")
                    response_data = getattr(func_resp, "response", None)

                    logger.info(f"[MAIN-UI] Found function_response: {func_name}")

                    if func_name == "generative_ui" and isinstance(response_data, dict):
                        ui_payload = response_data.get("ui_payload")
                        if ui_payload:
                            logger.info(
                                f"[MAIN-UI] >>> Detected ui_payload: component={ui_payload.get('type', 'unknown')}"
                            )
                            ui_event = {
                                "type": "generative_ui",
                                "component": ui_payload.get("type"),
                                "props": ui_payload.get("props", {}),
                            }
                            try:
                                await websocket.send_text(json.dumps(ui_event))
                                logger.info(
                                    f"[MAIN-UI] <<< SENT generative_ui WebSocket event: {ui_payload.get('type')}"
                                )
                            except (RuntimeError, WebSocketDisconnect):
                                logger.warning(
                                    "[MAIN-UI] WebSocket closed while sending UI event"
                                )

        event_json = event.model_dump_json(exclude_none=True, by_alias=True)
        if hasattr(event, "server_content") and event.server_content:
            logger.debug(
                "[TRANSCRIPTION-JSON-SAMPLE] Sending event with serverContent fields"
            )

        logger.debug("Sending event to client")
        try:
            await websocket.send_text(event_json)
        except (RuntimeError, WebSocketDisconnect):
            logger.info("WebSocket connection closed, stopping downstream_task")
            return False

        try:
            await session_manager.save_agent_session_to_db(
                unified_session_id, session.state, user_id=user_id
            )
        except Exception as e:
            logger.warning(f"Failed to persist session state: {e}")

        return True

    async def downstream_task() -> None:
        """Receives Events from run_live() and sends to WebSocket."""
        logger.debug("downstream_task started")
        max_retries = 2
        model_name = str(agent.model)
        logger.info(f"[LIVE] Attempting run_live with conversation model: {model_name}")

        for attempt in range(1, max_retries + 1):
            try:
                async for event in runner.run_live(
                    user_id=user_id,
                    session_id=unified_session_id,
                    live_request_queue=live_request_queue,
                    run_config=run_config,
                ):
                    should_continue = await _process_downstream_event(event)
                    if not should_continue:
                        return
                return
            except Exception as e:
                is_last_attempt = attempt == max_retries
                if _is_live_transient_error(e) and not is_last_attempt:
                    backoff_seconds = 2 ** (attempt - 1)
                    logger.warning(
                        f"[LIVE] Transient live error with model '{model_name}' "
                        f"(attempt {attempt}/{max_retries}): {e}. "
                        f"Retrying in {backoff_seconds}s."
                    )
                    await asyncio.sleep(backoff_seconds)
                    continue
                raise

    async def ui_event_task() -> None:
        """Reads UI events from queue and sends to WebSocket."""
        logger.info("ui_event_task started")
        while True:
            try:
                # Wait for UI event with timeout to allow checking for disconnect
                ui_event = await asyncio.wait_for(ui_event_queue.get(), timeout=1.0)
                logger.info(f"[UI-TASK] Got UI event from queue: {ui_event.get('component')}")
                try:
                    await websocket.send_text(json.dumps(ui_event))
                    logger.info(f"[UI-TASK] <<< SENT generative_ui WebSocket event: {ui_event.get('component')}")
                except (RuntimeError, WebSocketDisconnect):
                    logger.info("WebSocket closed, stopping ui_event_task")
                    break
            except asyncio.TimeoutError:
                # Check if we should stop
                if websocket.client_state.name != "CONNECTED":
                    logger.info("WebSocket no longer connected, stopping ui_event_task")
                    break
            except Exception as e:
                logger.error(f"Error in ui_event_task: {e}")
                break

    # Run all three tasks concurrently
    try:
        await asyncio.gather(upstream_task(), downstream_task(), ui_event_task())
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"Error in streaming: {e}", exc_info=True)
    finally:
        logger.info("Closing live_request_queue")
        live_request_queue.close()
        set_ui_event_queue(None)  # Clear the queue reference
        
        # ========================================
        # POST-CONVERSATION THINKING TURN
        # ========================================
        logger.info("🧠 [POST-CONVERSATION] Triggering thinking mode after conversation ended")
        
        try:
            # Trigger thinking mode to:
            # 1. Review what happened in the conversation
            # 2. Check calendar for what's next
            # 3. Set appropriate timer for next intervention
            # Minimal trigger - agent uses tools to understand context
            trigger_context = "Conversation just ended."
            
            async for _ in AgentRuntime.run_thinking_mode(
                user_id=user_id,
                trigger_context=trigger_context,
                session_manager=session_manager,
                timezone=_normalize_timezone(session.state.get("user_timezone"), "UTC"),
            ):
                pass  # We don't need to process the events, just let it run
            
            logger.info("✅ [POST-CONVERSATION] Thinking turn completed successfully")
            
        except Exception as thinking_error:
            logger.error(
                f"❌ [POST-CONVERSATION] Failed to run thinking turn: {thinking_error}",
                exc_info=True
            )
            # Don't raise - we don't want to fail the WebSocket close due to this


# ========================================
# Main Entry Point
# ========================================

if __name__ == "__main__":
    import uvicorn
    # Get host and port from environment variables with defaults
    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8000"))
    
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
