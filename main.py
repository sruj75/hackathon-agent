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
from typing import Optional
from zoneinfo import ZoneInfo

from composio import Composio
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
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

from auth import AuthUser, get_authenticated_user, verify_supabase_jwt
from repos import event_repo, user_repo
from datetime import datetime, timedelta, time, timezone
from agent_runtime import AgentRuntime
import cron_service
from db import close_pool

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


def _get_composio_client() -> Composio:
    api_key = os.getenv("COMPOSIO_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="COMPOSIO_API_KEY is not configured",
        )
    return Composio(api_key=api_key)


def _composio_apps() -> list[str]:
    raw_apps = os.getenv(
        "COMPOSIO_REQUIRED_APPS",
        "googlecalendar,googletasks",
    )
    return [name.strip() for name in raw_apps.split(",") if name.strip()]


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


def _normalize_timezone(timezone_name: str | None) -> str | None:
    """Return valid IANA timezone or None when unavailable/invalid."""
    if not timezone_name:
        return None
    try:
        ZoneInfo(timezone_name)
        return timezone_name
    except Exception:
        logger.warning("Invalid timezone '%s'.", timezone_name)
        return None


def _require_timezone(timezone_name: str | None, *, context: str) -> str:
    normalized = _normalize_timezone(timezone_name)
    if not normalized:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "missing_timezone",
                "context": context,
                "message": "A valid IANA timezone is required.",
            },
        )
    return normalized


def _next_morning_wake_datetime(
    user_timezone: str, wake_time_raw: str | None
) -> datetime | None:
    """
    Compute next local morning wake datetime for a user.
    If today's wake time already passed, schedule for tomorrow.
    """
    tz = ZoneInfo(user_timezone)
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
    """Best-effort conversion for persisted datetime fields."""
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
            or _normalize_timezone(existing_payload.get("timezone")) != user_timezone
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
                    "schedule_owner": "system",
                    "schedule_policy": "morning_bootstrap",
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
            "schedule_owner": "system",
            "schedule_policy": "morning_bootstrap",
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
    Backfill cron jobs for missing system-owned morning bootstrap events only.

    Autonomy boundary:
    - We do NOT recreate arbitrary future timers at startup.
    - We only repair the deterministic morning wake invariant.
    """
    missing_events = await event_repo.list_future_unexecuted_events_missing_cron(limit=200)
    if not missing_events:
        return

    logger.info(
        f"[cron-reconcile] Evaluating {len(missing_events)} events missing cron_job_id "
        "for morning-bootstrap reconciliation"
    )
    for event in missing_events:
        try:
            event_id = _event_field(event, "id")
            if not event_id:
                continue
            event_type = _event_field(event, "event_type", "")
            payload = _event_field(event, "payload", {}) or {}
            owner = payload.get("schedule_owner")
            reason = payload.get("reason")
            policy = payload.get("schedule_policy")
            if event_type != "morning_wake":
                continue
            if owner not in ("system", None):
                continue
            if reason != "daily_bootstrap":
                continue
            if policy not in ("morning_bootstrap", None):
                continue

            scheduled_time = _as_datetime(_event_field(event, "scheduled_time"))
            if not scheduled_time:
                continue

            timezone_name = payload.get("timezone")
            if not timezone_name:
                profile = await user_repo.get_profile(_event_field(event, "user_id", ""))
                timezone_name = (profile or {}).get("timezone")
            timezone_name = _normalize_timezone(timezone_name)
            if not timezone_name:
                logger.warning(
                    "[cron-reconcile] Skipping event %s due to missing timezone",
                    event_id,
                )
                continue

            cron_job_id = await cron_service.create_one_time_job(
                target_datetime=scheduled_time,
                event_id=event_id,
                timezone=timezone_name,
            )
            await event_repo.update_cron_job_id(event_id, cron_job_id)
            payload_updates = dict(payload)
            payload_updates.setdefault("schedule_owner", "system")
            payload_updates.setdefault("schedule_policy", "morning_bootstrap")
            await event_repo.update_event(event_id, payload=payload_updates)
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

@app.get("/api/preferences/me")
async def get_user_preferences(current_user: AuthUser = Depends(get_authenticated_user)):
    """Read persisted user preferences."""
    user_id = current_user.user_id
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


@app.put("/api/preferences/me")
async def update_user_preferences(
    payload: dict,
    current_user: AuthUser = Depends(get_authenticated_user),
):
    """Create/update user preferences and re-sync morning wake scheduling."""
    user_id = current_user.user_id
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
# Composio Integration Endpoints
# ========================================

@app.post("/api/integrations/composio/connect-link")
async def create_composio_connect_link(
    payload: dict,
    current_user: AuthUser = Depends(get_authenticated_user),
):
    redirect_url_raw = payload.get("redirect_url")
    requested_app = payload.get("app")
    redirect_url = redirect_url_raw if isinstance(redirect_url_raw, str) and redirect_url_raw.strip() else None
    apps = [requested_app] if isinstance(requested_app, str) and requested_app.strip() else _composio_apps()
    try:
        entity = _get_composio_client().get_entity(current_user.user_id)
        links: list[dict] = []
        for app_name in apps:
            request = entity.initiate_connection(
                app_name=app_name,
                redirect_url=redirect_url,
            )
            links.append(
                {
                    "app": app_name,
                    "connection_status": request.connectionStatus,
                    "connected_account_id": request.connectedAccountId,
                    "redirect_url": request.redirectUrl,
                }
            )
        return {
            "status": "ok",
            "user_id": current_user.user_id,
            "links": links,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to create Composio connect links")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/integrations/composio/status")
async def get_composio_status(current_user: AuthUser = Depends(get_authenticated_user)):
    try:
        entity = _get_composio_client().get_entity(current_user.user_id)
        connections = entity.get_connections()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to read Composio connection status")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    required_apps = _composio_apps()
    statuses: list[dict] = []
    for app_name in required_apps:
        app_connections = [
            conn
            for conn in connections
            if str(getattr(conn, "appName", "")).lower() == app_name.lower()
        ]
        active_conn = next(
            (
                conn
                for conn in app_connections
                if str(getattr(conn, "status", "")).upper() == "ACTIVE"
            ),
            None,
        )
        statuses.append(
            {
                "app": app_name,
                "connected": active_conn is not None,
                "status": str(getattr(active_conn, "status", "NOT_CONNECTED")),
                "connected_account_id": getattr(active_conn, "id", None),
            }
        )

    return {
        "status": "ok",
        "user_id": current_user.user_id,
        "apps": statuses,
        "all_connected": all(item["connected"] for item in statuses),
    }


# ========================================
# Cron Endpoints
# ========================================


@app.post("/api/execute-event/{event_id}")
async def execute_event(event_id: str):
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
    event_timezone = _normalize_timezone(payload.get("timezone"))
    if not event_timezone:
        try:
            profile = await user_repo.get_profile(_event_field(event, "user_id", ""))
        except Exception as profile_error:
            logger.warning(
                f"[execute-event] Failed to load profile timezone for {_event_field(event, 'user_id', '')}: {profile_error}"
            )
            profile = None
        event_timezone = _normalize_timezone((profile or {}).get("timezone"))
    if not event_timezone:
        raise HTTPException(
            status_code=400,
            detail={
                "status": "missing_timezone",
                "event_id": event_id,
                "message": "Cannot execute scheduled event without user timezone.",
            },
        )

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
    payload: dict,
    current_user: AuthUser = Depends(get_authenticated_user),
):
    """
    Saves the user's Expo push token.
    Payload expected: {"token": "..."}
    """

    user_id = current_user.user_id
    token = payload.get("token")
    
    if not token:
        raise HTTPException(status_code=400, detail="Missing token")
    
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
    2) Optionally reconcile missing morning-bootstrap cron jobs only
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

    if os.getenv("ENABLE_MORNING_CRON_RECONCILE", "false").lower() in (
        "1",
        "true",
        "yes",
    ):
        try:
            await _reconcile_missing_cron_jobs()
        except Exception as e:
            logger.warning(f"[bootstrap] Failed cron reconciliation: {e}")


@app.on_event("shutdown")
async def shutdown_cleanup() -> None:
    """Cleanup async resources."""
    try:
        await close_pool()
    except Exception as e:
        logger.warning(f"[shutdown] Failed to close DB pool: {e}")


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """WebSocket endpoint for bidirectional streaming with Supabase-authenticated init."""
    logger.info("WebSocket connection request: session_id=%s", session_id)
    await websocket.accept()
    logger.info("WebSocket connection accepted")

    user_id: Optional[str] = None
    unified_session_id: Optional[str] = None
    session = None
    live_request_queue = LiveRequestQueue()
    ui_event_queue = asyncio.Queue()
    set_ui_event_queue(ui_event_queue)
    run_config = AgentRuntime.get_conversation_mode_config()

    try:
        first_message = await asyncio.wait_for(websocket.receive(), timeout=20.0)
        if first_message.get("type") == "websocket.disconnect":
            logger.info("WebSocket disconnected before init")
            return
        if first_message.get("text") is None:
            await websocket.close(code=4400, reason="init payload required")
            return

        try:
            init_message = json.loads(first_message["text"])
        except json.JSONDecodeError:
            await websocket.close(code=4400, reason="invalid init payload")
            return

        if init_message.get("type") != "init":
            await websocket.close(code=4400, reason="first message must be init")
            return

        access_token = init_message.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            await websocket.close(code=4401, reason="missing access_token")
            return

        try:
            auth_user = await verify_supabase_jwt(access_token.strip())
        except HTTPException:
            await websocket.close(code=4401, reason="invalid access_token")
            return

        user_id = auth_user.user_id
        resume_session_id = init_message.get("resume_session_id")
        requested_session_id = (
            resume_session_id
            if isinstance(resume_session_id, str) and resume_session_id.strip()
            else session_id
        )
        unified_session_id = _select_ws_session_id(user_id, requested_session_id)

        logger.info(
            "[WS-INIT] Authenticated websocket user=%s, session=%s",
            user_id,
            unified_session_id,
        )

        current_user_id.set(user_id)
        current_session_id.set(unified_session_id)

        session = await session_manager.get_or_create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=unified_session_id,
        )

        try:
            profile = await user_repo.get_profile(user_id)
        except Exception as profile_error:
            logger.warning("[WS-INIT] Failed to load profile for %s: %s", user_id, profile_error)
            profile = None

        trigger_type = init_message.get("trigger_type")
        if isinstance(trigger_type, str) and trigger_type:
            session.state["trigger_type"] = trigger_type

        client_timezone = _normalize_timezone(init_message.get("timezone"))
        profile_timezone = _normalize_timezone((profile or {}).get("timezone"))
        resolved_timezone = client_timezone or profile_timezone
        current_user_timezone.set(resolved_timezone)
        if resolved_timezone:
            session.state["user_timezone"] = resolved_timezone
            try:
                await user_repo.update_profile(user_id, timezone=resolved_timezone)
            except Exception as profile_update_error:
                logger.warning(
                    "[WS-INIT] Failed to persist timezone for %s: %s",
                    user_id,
                    profile_update_error,
                )

        activation_message = types.Content(
            role="user",
            parts=[
                types.Part(text="Start with a brief greeting, then ask how you can help."),
            ],
        )
        live_request_queue.send_content(activation_message)
        logger.info("[WS-INIT] Sent activation message")

        async def upstream_task() -> None:
            """Receives messages from WebSocket and sends to LiveRequestQueue."""
            logger.debug("upstream_task started")
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        logger.info("WebSocket disconnect received in upstream_task")
                        break

                    audio_data = message.get("bytes")
                    if audio_data is not None:
                        audio_blob = types.Blob(
                            mime_type="audio/pcm;rate=16000",
                            data=audio_data,
                        )
                        live_request_queue.send_realtime(audio_blob)
                        continue

                    text_data = message.get("text")
                    if text_data is None:
                        continue

                    try:
                        json_message = json.loads(text_data)
                    except json.JSONDecodeError:
                        logger.warning("Invalid JSON received")
                        continue

                    if json_message.get("type") == "text":
                        content = types.Content(parts=[types.Part(text=json_message["text"])])
                        live_request_queue.send_content(content)
            except Exception as e:
                logger.debug("upstream_task ended: %s", e)

        async def _process_downstream_event(event) -> bool:
            """Process one ADK event and forward to frontend. Returns False on closed socket."""
            if hasattr(event, "server_content") and event.server_content:
                if (
                    hasattr(event.server_content, "input_transcription")
                    and event.server_content.input_transcription
                ):
                    logger.info(
                        "[TRANSCRIPTION-INPUT] User: %s",
                        event.server_content.input_transcription.text,
                    )

                if (
                    hasattr(event.server_content, "output_transcription")
                    and event.server_content.output_transcription
                ):
                    logger.info(
                        "[TRANSCRIPTION-OUTPUT] Agent: %s",
                        event.server_content.output_transcription.text,
                    )

            if event.content and event.content.parts:
                for i, part in enumerate(event.content.parts):
                    part_attrs = [
                        a
                        for a in ["text", "function_call", "function_response", "inline_data"]
                        if getattr(part, a, None) is not None
                    ]
                    if part_attrs:
                        logger.info("[MAIN-EVENT] Part %s has: %s", i, part_attrs)

                    func_resp = getattr(part, "function_response", None)
                    if func_resp is None:
                        continue
                    func_name = getattr(func_resp, "name", "unknown")
                    response_data = getattr(func_resp, "response", None)
                    if func_name == "generative_ui" and isinstance(response_data, dict):
                        ui_payload = response_data.get("ui_payload")
                        if ui_payload:
                            ui_event = {
                                "type": "generative_ui",
                                "component": ui_payload.get("type"),
                                "props": ui_payload.get("props", {}),
                            }
                            try:
                                await websocket.send_text(json.dumps(ui_event))
                            except (RuntimeError, WebSocketDisconnect):
                                logger.warning("[MAIN-UI] WebSocket closed while sending UI event")

            event_json = event.model_dump_json(exclude_none=True, by_alias=True)
            try:
                await websocket.send_text(event_json)
            except (RuntimeError, WebSocketDisconnect):
                logger.info("WebSocket connection closed, stopping downstream_task")
                return False

            try:
                assert unified_session_id is not None
                assert user_id is not None
                await session_manager.save_agent_session_to_db(
                    unified_session_id,
                    session.state,
                    user_id=user_id,
                )
            except Exception as e:
                logger.warning("Failed to persist session state: %s", e)

            return True

        async def downstream_task() -> None:
            """Receives Events from run_live() and sends to WebSocket."""
            logger.debug("downstream_task started")
            max_retries = 2
            model_name = str(agent.model)
            logger.info("[LIVE] Attempting run_live with conversation model: %s", model_name)

            for attempt in range(1, max_retries + 1):
                try:
                    assert user_id is not None
                    assert unified_session_id is not None
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
                            "[LIVE] Transient live error with model '%s' (attempt %s/%s): %s. "
                            "Retrying in %ss.",
                            model_name,
                            attempt,
                            max_retries,
                            e,
                            backoff_seconds,
                        )
                        await asyncio.sleep(backoff_seconds)
                        continue
                    raise

        async def ui_event_task() -> None:
            """Reads UI events from queue and sends to WebSocket."""
            while True:
                try:
                    ui_event = await asyncio.wait_for(ui_event_queue.get(), timeout=1.0)
                    try:
                        await websocket.send_text(json.dumps(ui_event))
                    except (RuntimeError, WebSocketDisconnect):
                        logger.info("WebSocket closed, stopping ui_event_task")
                        break
                except asyncio.TimeoutError:
                    if websocket.client_state.name != "CONNECTED":
                        break
                except Exception as e:
                    logger.error("Error in ui_event_task: %s", e)
                    break

        try:
            await asyncio.gather(upstream_task(), downstream_task(), ui_event_task())
        except WebSocketDisconnect:
            logger.info("Client disconnected")
        except Exception as e:
            logger.error("Error in streaming: %s", e, exc_info=True)
    finally:
        logger.info("Closing live_request_queue")
        live_request_queue.close()
        set_ui_event_queue(None)

        if session is not None and user_id:
            logger.info("🧠 [POST-CONVERSATION] Triggering thinking mode after conversation ended")
            try:
                trigger_context = "Conversation just ended."
                async for _ in AgentRuntime.run_thinking_mode(
                    user_id=user_id,
                    trigger_context=trigger_context,
                    session_manager=session_manager,
                    timezone=_normalize_timezone(session.state.get("user_timezone")),
                ):
                    pass
                logger.info("✅ [POST-CONVERSATION] Thinking turn completed successfully")
            except Exception as thinking_error:
                logger.error(
                    "❌ [POST-CONVERSATION] Failed to run thinking turn: %s",
                    thinking_error,
                    exc_info=True,
                )


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
