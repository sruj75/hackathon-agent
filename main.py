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
import time
import warnings
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from composio import Composio
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Header
from fastapi.middleware.cors import CORSMiddleware

# Load environment variables BEFORE importing agent
load_dotenv(Path(__file__).parent / ".env")

# Add the current directory (agent/) to sys.path so that absolute imports work correctly
import sys
sys.path.insert(0, str(Path(__file__).parent))

# Import agent after loading env
from voice_agent.agent import conversation_agent as main_agent  # noqa: E402
from voice_agent.onboarding_agent import onboarding_agent  # noqa: E402
from voice_agent.render_ui_tools import set_ui_event_queue  # noqa: E402

from google.adk.runners import Runner
from session_manager import ADKSessionManager
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.genai import types
from context import current_session_id, current_user_id, current_user_timezone

from auth import AuthUser, get_authenticated_user, verify_supabase_jwt
from repos import event_repo, user_repo
from datetime import datetime, timedelta, timezone
from agent_runtime import AgentRuntime
import cron_service
from db import close_pool
from notification_service import send_push_notification
from onboarding_service import complete_onboarding_for_user

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

HHMM_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
SESSION_ID_PATTERN = re.compile(r"^session_(?P<user_id>.+)_(?P<date>\d{4}-\d{2}-\d{2})$")
ONBOARDING_SESSION_ID_PATTERN = re.compile(r"^session_onboarding_(?P<user_id>.+)$")


def _event_field(event: object, field: str, default=None):
    """Read an event field from either a dict record or object-style record."""
    if isinstance(event, dict):
        return event.get(field, default)
    return getattr(event, field, default)


def _as_json_object(value: object, *, context: str) -> dict[str, Any]:
    """Normalize mixed JSON payload shapes to an object."""
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("[%s] Expected JSON object payload, got non-JSON string", context)
            return {}
        if isinstance(decoded, dict):
            return decoded
        logger.warning(
            "[%s] Expected JSON object payload, got decoded %s",
            context,
            type(decoded).__name__,
        )
        return {}

    logger.warning(
        "[%s] Expected JSON object payload, got %s",
        context,
        type(value).__name__,
    )
    return {}


def _env_flag_enabled(name: str, default: str) -> bool:
    """Parse common boolean env flags."""
    return os.getenv(name, default).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class LifecycleSettings:
    enable_reliability_bootstrap: bool
    enable_morning_cron_reconcile: bool


def _read_lifecycle_settings() -> LifecycleSettings:
    return LifecycleSettings(
        enable_reliability_bootstrap=_env_flag_enabled(
            "ENABLE_RELIABILITY_BOOTSTRAP", "true"
        ),
        enable_morning_cron_reconcile=_env_flag_enabled(
            "ENABLE_MORNING_CRON_RECONCILE", "false"
        ),
    )

# Suppress Pydantic serialization warnings
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# Application name constant
APP_NAME = "intentive-coach"
COMPOSIO_INTEGRATION_APPS = ("googlecalendar", "googletasks")
ONBOARDING_STATUS_PENDING = "pending"
ONBOARDING_STATUS_COMPLETED = "completed"
LIFECYCLE_NEEDS_CONNECT_FLOW = "needs_connect_flow"
LIFECYCLE_NEEDS_ONBOARDING = "needs_onboarding"
LIFECYCLE_ACTIVE = "active"
ENTRY_MODE_REACTIVE = "reactive"
ENTRY_MODE_PROACTIVE = "proactive"
ENTRY_MODE_POST_ONBOARDING = "post_onboarding"
ONBOARDING_STATE_PERSIST_INTERVAL_SECONDS = 1.5

# ========================================
# FastAPI App Setup
# ========================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await lifecycle_manager.startup()
    app.state.lifecycle = lifecycle_manager
    try:
        yield
    finally:
        await lifecycle_manager.shutdown()

app = FastAPI(
    title="Intentive Voice Agent API",
    description="Real-time voice AI coaching with Gemini Live API",
    version="1.0.0",
    lifespan=lifespan,
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
main_runner = Runner(
    app_name=APP_NAME,
    agent=main_agent,
    session_service=session_manager.service,
)
onboarding_runner = Runner(
    app_name=APP_NAME,
    agent=onboarding_agent,
    session_service=session_manager.service,
)
# Backward compatibility for existing tests/modules still referencing `main.runner`.
runner = main_runner


def _get_composio_client() -> Composio:
    api_key = os.getenv("COMPOSIO_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="COMPOSIO_API_KEY is not configured",
        )
    return Composio(api_key=api_key)


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


def _safe_model_dump(value: object) -> dict[str, Any]:
    """Best-effort serialization for diagnostic logging."""
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(by_alias=True, exclude_none=True)
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            return {}
    return {}


def _schema_has_additional_properties(schema: object) -> bool:
    if isinstance(schema, dict):
        return "additionalProperties" in schema or "additional_properties" in schema
    return False


def _log_live_setup_diagnostics(
    *,
    selected_agent: object,
    run_config: object,
    onboarding_mode: bool,
    user_id: str,
    session_id: str,
) -> None:
    """Emit compact diagnostics for Live setup payload shapes."""
    run_config_dump = _safe_model_dump(run_config)
    logger.warning(
        "[LIVE-DIAG] setup model=%s onboarding_mode=%s user=%s session=%s run_config_keys=%s",
        getattr(selected_agent, "model", None),
        onboarding_mode,
        user_id,
        session_id,
        sorted(run_config_dump.keys()),
    )

    tools = getattr(selected_agent, "tools", None)
    if not isinstance(tools, list):
        logger.warning("[LIVE-DIAG] setup tools unavailable type=%s", type(tools).__name__)
        return

    for idx, tool in enumerate(tools):
        declaration = None
        if hasattr(tool, "_get_declaration"):
            try:
                declaration = tool._get_declaration()  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning(
                    "[LIVE-DIAG] setup tool idx=%s name=%s declaration_error=%s",
                    idx,
                    getattr(tool, "name", type(tool).__name__),
                    exc,
                )
                continue

        declaration_dump = _safe_model_dump(declaration)
        params_schema = declaration_dump.get("parametersJsonSchema")
        response_schema = declaration_dump.get("responseJsonSchema")
        logger.warning(
            "[LIVE-DIAG] tool idx=%s name=%s type=%s decl_keys=%s params_schema_keys=%s "
            "response_schema_keys=%s params_has_additional_properties=%s "
            "response_has_additional_properties=%s",
            idx,
            declaration_dump.get("name", getattr(tool, "name", type(tool).__name__)),
            type(tool).__name__,
            sorted(declaration_dump.keys()),
            sorted(params_schema.keys()) if isinstance(params_schema, dict) else [],
            sorted(response_schema.keys()) if isinstance(response_schema, dict) else [],
            _schema_has_additional_properties(params_schema),
            _schema_has_additional_properties(response_schema),
        )


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


def _normalize_onboarding_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if status == ONBOARDING_STATUS_COMPLETED:
        return ONBOARDING_STATUS_COMPLETED
    return ONBOARDING_STATUS_PENDING


def _resolve_user_lifecycle_state(
    *,
    profile: dict | None,
    all_connected: bool | None,
) -> str:
    """
    Resolve lifecycle from server state.

    `all_connected` may be unknown (None) in contexts where integration status is
    not loaded; onboarding completeness remains authoritative in that case.
    """
    if all_connected is False:
        return LIFECYCLE_NEEDS_CONNECT_FLOW
    if _is_onboarding_complete(profile):
        return LIFECYCLE_ACTIVE
    return LIFECYCLE_NEEDS_ONBOARDING


def _lifecycle_state_to_route_hint(lifecycle_state: str) -> str:
    if lifecycle_state == LIFECYCLE_NEEDS_CONNECT_FLOW:
        return "connect_flow"
    if lifecycle_state == LIFECYCLE_NEEDS_ONBOARDING:
        return "onboarding"
    return "assistant"


def _is_onboarding_complete(profile: dict | None) -> bool:
    if not profile:
        return False

    onboarding_status = _normalize_onboarding_status(profile.get("onboarding_status"))
    if onboarding_status == ONBOARDING_STATUS_COMPLETED:
        return True

    if profile.get("onboarding_completed_at") is not None:
        return True

    # Backward compatibility: users onboarded before onboarding_status existed
    # should continue entering the main assistant when their profile is complete.
    return _profile_is_complete(profile)


def _get_onboarding_session_id(user_id: str) -> str:
    return f"session_onboarding_{user_id}"


def _select_onboarding_session_id(user_id: str, requested_session_id: str | None) -> str:
    expected = _get_onboarding_session_id(user_id)
    if not requested_session_id:
        return expected

    match = ONBOARDING_SESSION_ID_PATTERN.match(requested_session_id)
    if not match:
        return expected
    if match.group("user_id") != user_id:
        return expected
    return requested_session_id


def _current_local_date(timezone_name: str | None) -> str:
    tz: ZoneInfo | None = None
    if timezone_name:
        try:
            tz = ZoneInfo(timezone_name)
        except Exception:
            logger.warning(
                "Invalid timezone '%s' for date key, using local runtime timezone",
                timezone_name,
            )
    if tz is None:
        return datetime.now().astimezone().date().isoformat()
    return datetime.now(timezone.utc).astimezone(tz).date().isoformat()


def _select_ws_session_id(
    user_id: str,
    requested_session_id: str | None,
    timezone_name: str | None = None,
) -> str:
    """
    Select conversation session ID.

    Uses explicit session IDs only when they follow our deterministic pattern and
    belong to the same user; otherwise falls back to today's unified session.
    """
    fallback_session_id = ADKSessionManager.get_daily_session_id(
        user_id,
        timezone_name=timezone_name,
    )
    if not requested_session_id:
        return fallback_session_id

    match = SESSION_ID_PATTERN.match(requested_session_id)
    if not match:
        return fallback_session_id

    if match.group("user_id") != user_id:
        return fallback_session_id

    expected_date = _current_local_date(timezone_name)
    if match.group("date") != expected_date:
        return fallback_session_id

    return requested_session_id


def _use_onboarding_mode(lifecycle_state: str) -> bool:
    return lifecycle_state == LIFECYCLE_NEEDS_ONBOARDING


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


def _normalize_entry_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    if mode in (ENTRY_MODE_PROACTIVE, ENTRY_MODE_POST_ONBOARDING):
        return mode
    return ENTRY_MODE_REACTIVE


def _parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _build_entry_context(
    init_message: dict[str, Any],
    *,
    default_trigger_type: object,
    resolved_timezone: str | None,
) -> dict[str, Any]:
    trigger_type = (
        str(default_trigger_type).strip()
        if isinstance(default_trigger_type, str) and str(default_trigger_type).strip()
        else None
    )
    source = init_message.get("source")
    if not isinstance(source, str) or not source.strip():
        source = "manual"

    entry_mode_raw = init_message.get("entry_mode")
    entry_mode = _normalize_entry_mode(entry_mode_raw)
    if entry_mode == ENTRY_MODE_REACTIVE and source == "push":
        entry_mode = ENTRY_MODE_PROACTIVE
    if trigger_type == ENTRY_MODE_POST_ONBOARDING:
        entry_mode = ENTRY_MODE_POST_ONBOARDING

    context: dict[str, Any] = {
        "entry_mode": entry_mode,
        "source": source,
        "trigger_type": trigger_type,
        "event_id": init_message.get("event_id")
        if isinstance(init_message.get("event_id"), str)
        else None,
        "calendar_event_id": init_message.get("calendar_event_id")
        if isinstance(init_message.get("calendar_event_id"), str)
        else None,
        "scheduled_time": init_message.get("scheduled_time")
        if isinstance(init_message.get("scheduled_time"), str)
        else None,
        "timezone": resolved_timezone,
        "is_stale": False,
    }
    return context


def _is_entry_context_stale(entry_context: dict[str, Any], timezone_name: str | None) -> bool:
    if entry_context.get("entry_mode") != ENTRY_MODE_PROACTIVE:
        return False
    scheduled = _parse_iso_datetime(entry_context.get("scheduled_time"))
    if not scheduled:
        return False
    if not timezone_name:
        return False
    try:
        tz = ZoneInfo(timezone_name)
    except Exception:
        return False

    scheduled_day = scheduled.astimezone(tz).date().isoformat()
    current_day = datetime.now(timezone.utc).astimezone(tz).date().isoformat()
    return scheduled_day != current_day


def _playbook_summary(playbook: dict[str, Any] | object) -> dict[str, Any]:
    source = playbook if isinstance(playbook, dict) else {}
    return {
        "summary": source.get("summary") if isinstance(source.get("summary"), str) else "",
        "struggles": _normalize_health_anchors(source.get("struggles")),
        "goals": _normalize_health_anchors(source.get("goals")),
        "communication_style": source.get("communication_style")
        if isinstance(source.get("communication_style"), str)
        else "",
    }


def _build_profile_context(profile: dict | None) -> dict[str, Any]:
    source = profile or {}
    return {
        "preferences": {
            "wake_time": source.get("wake_time"),
            "bedtime": source.get("bedtime"),
            "timezone": source.get("timezone"),
            "health_anchors": _normalize_health_anchors(source.get("health_anchors")),
        },
        "playbook": _playbook_summary(source.get("playbook")),
        "onboarding_status": _normalize_onboarding_status(source.get("onboarding_status")),
    }


def _build_main_activation_prompt(entry_context: dict[str, Any]) -> str:
    entry_mode = entry_context.get("entry_mode")
    trigger_type = entry_context.get("trigger_type")
    event_id = entry_context.get("event_id")
    is_stale = bool(entry_context.get("is_stale"))

    if entry_mode == ENTRY_MODE_POST_ONBOARDING:
        return (
            "Start main-agent mode after onboarding completion. Ground on the user's local time, "
            "schedule, and onboarding profile context. If it's late, guide a wind-down routine; "
            "otherwise, help plan the remainder of today with prioritized timeboxed essentials."
        )

    if entry_mode == ENTRY_MODE_PROACTIVE and not is_stale:
        return (
            "Start proactive check-in mode. Use current time, schedule, and profile context. "
            f"Trigger type: {trigger_type or 'unknown'}. Event id: {event_id or 'n/a'}. "
            "Open by addressing the specific transition intention for this check-in, then drive the "
            "next concrete action."
        )

    if entry_mode == ENTRY_MODE_PROACTIVE and is_stale:
        return (
            "A stale notification was opened. Do not revive old context. Reset to present-moment "
            "support and help the user get back on track based on current time and schedule."
        )

    return (
        "Start reactive mode. Ground on current time, schedule, and profile context, then ask what "
        "the user needs right now."
    )


def _validate_onboarding_complete_payload(payload: dict) -> tuple[dict, list[str]]:
    body = payload if isinstance(payload, dict) else {}
    errors: list[str] = []
    sanitized: dict[str, object] = {}
    wake_time = body.get("wake_time")
    bedtime = body.get("bedtime")
    timezone_name = body.get("timezone")
    health_anchors = _normalize_health_anchors(body.get("health_anchors"))

    if not isinstance(wake_time, str) or not _is_valid_hhmm(wake_time):
        errors.append("wake_time must be HH:MM in 24-hour format")
    else:
        sanitized["wake_time"] = wake_time

    if not isinstance(bedtime, str) or not _is_valid_hhmm(bedtime):
        errors.append("bedtime must be HH:MM in 24-hour format")
    else:
        sanitized["bedtime"] = bedtime

    if timezone_name is not None:
        if not isinstance(timezone_name, str) or not _normalize_timezone(timezone_name):
            errors.append("timezone must be a valid IANA timezone")
        else:
            sanitized["timezone"] = timezone_name

    if "health_anchors" in body:
        sanitized["health_anchors"] = health_anchors

    if "playbook" in body:
        playbook = body.get("playbook")
        if playbook is None:
            sanitized["playbook"] = {}
        elif isinstance(playbook, dict):
            sanitized["playbook"] = playbook
        else:
            errors.append("playbook must be a JSON object")

    return sanitized, errors


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
        existing_payload = _as_json_object(
            _event_field(existing, "payload", {}),
            context=f"morning-bootstrap event payload user={user_id}",
        )
        existing_cron_job_id = _event_field(existing, "cron_job_id")

        # Reschedule when wake time/timezone changed, or when cron id is missing.
        should_reschedule = (
            not existing_cron_job_id
            or not existing_scheduled
            or abs((existing_scheduled - target_local).total_seconds()) >= 60
            or _normalize_timezone(existing_payload.get("timezone")) != user_timezone
        )

        if should_reschedule:
            # Create-first: ensure event always has a cron job; delete old only after successful update
            cron_job_id = await cron_service.create_one_time_job(
                target_datetime=target_local,
                event_id=event_id,
                timezone=user_timezone,
            )
            try:
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
            except Exception as update_error:
                # Rollback: delete newly created orphan
                try:
                    await cron_service.delete_job(cron_job_id)
                except Exception as cleanup_error:
                    logger.warning(
                        f"[morning-bootstrap] Failed to cleanup orphan cron job {cron_job_id} after update failed: {cleanup_error}"
                    )
                raise update_error

            if existing_cron_job_id:
                try:
                    await cron_service.delete_job(existing_cron_job_id)
                except Exception as cleanup_error:
                    logger.warning(
                        f"[morning-bootstrap] Failed to cleanup previous cron job {existing_cron_job_id}: {cleanup_error}"
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
            payload = _as_json_object(
                _event_field(event, "payload", {}),
                context=f"cron-reconcile event payload event_id={event_id}",
            )
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


async def _get_composio_status_payload(user_id: str) -> dict:
    entity = _get_composio_client().get_entity(user_id)
    connections = entity.get_connections()

    statuses: list[dict] = []
    for app_name in COMPOSIO_INTEGRATION_APPS:
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
        "apps": statuses,
        "all_connected": all(item["connected"] for item in statuses),
    }


def _bootstrap_route_hint(*, all_connected: bool, profile: dict | None) -> str:
    lifecycle_state = _resolve_user_lifecycle_state(
        profile=profile,
        all_connected=all_connected,
    )
    return _lifecycle_state_to_route_hint(lifecycle_state)


async def _complete_onboarding_workflow(
    *,
    user_id: str,
    wake_time: str,
    bedtime: str,
    timezone_name: str | None,
    playbook: dict[str, Any] | None,
    health_anchors: list[str] | None,
) -> dict[str, Any]:
    updated_profile, completion_time = await complete_onboarding_for_user(
        user_id,
        wake_time=wake_time,
        bedtime=bedtime,
        timezone_name=timezone_name,
        playbook=playbook,
        health_anchors=health_anchors,
    )

    scheduler_error = None
    if _profile_is_complete(updated_profile):
        try:
            await _ensure_morning_wake_for_user(updated_profile)
        except Exception as exc:
            scheduler_error = str(exc)
            logger.warning(
                "[onboarding] Failed to resync morning wake for %s: %s",
                user_id,
                exc,
            )

    try:
        composio_payload = await _get_composio_status_payload(user_id)
    except Exception:
        composio_payload = {"apps": [], "all_connected": False}

    route_hint = _bootstrap_route_hint(
        all_connected=composio_payload["all_connected"],
        profile=updated_profile,
    )
    return {
        "status": "ok" if scheduler_error is None else "partial_success",
        "user_id": user_id,
        "onboarding_status": ONBOARDING_STATUS_COMPLETED,
        "onboarding_completed_at": completion_time.isoformat(),
        "preferences": {
            "wake_time": updated_profile.get("wake_time"),
            "bedtime": updated_profile.get("bedtime"),
            "timezone": updated_profile.get("timezone"),
            "health_anchors": _normalize_health_anchors(
                updated_profile.get("health_anchors")
            ),
        },
        "route_hint": route_hint,
        "handoff_to_main": route_hint == "assistant",
        "scheduler": {
            "resynced": scheduler_error is None,
            "error": scheduler_error,
        },
    }


# ========================================
# Endpoints
# ========================================

@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "app": APP_NAME, "agent": main_agent.name}


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
    body = payload if isinstance(payload, dict) else {}
    if "app" in body:
        raise HTTPException(status_code=400, detail="app selection is not supported")
    redirect_url_raw = body.get("redirect_url")
    redirect_url = (
        redirect_url_raw
        if isinstance(redirect_url_raw, str) and redirect_url_raw.strip()
        else None
    )
    try:
        entity = _get_composio_client().get_entity(current_user.user_id)
        links: list[dict] = []
        for app_name in COMPOSIO_INTEGRATION_APPS:
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
        composio_payload = await _get_composio_status_payload(current_user.user_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to read Composio connection status")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "status": "ok",
        "user_id": current_user.user_id,
        "apps": composio_payload["apps"],
        "all_connected": composio_payload["all_connected"],
    }


# ========================================
# Onboarding Endpoints
# ========================================

@app.get("/api/onboarding/bootstrap")
async def get_onboarding_bootstrap(
    current_user: AuthUser = Depends(get_authenticated_user),
):
    user_id = current_user.user_id
    profile = await user_repo.get_profile(user_id)
    profile_exists = profile is not None

    # Enforce profile invariant for authenticated users.
    if profile is None:
        profile = await user_repo.update_profile(
            user_id,
            onboarding_status=ONBOARDING_STATUS_PENDING,
        )

    try:
        composio_payload = await _get_composio_status_payload(user_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to read onboarding bootstrap connection status")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    onboarding_completed = _is_onboarding_complete(profile)
    onboarding_status = (
        ONBOARDING_STATUS_COMPLETED
        if onboarding_completed
        else ONBOARDING_STATUS_PENDING
    )
    route_hint = _bootstrap_route_hint(
        all_connected=composio_payload["all_connected"],
        profile=profile,
    )

    completed_at = _as_datetime(profile.get("onboarding_completed_at"))
    return {
        "status": "ok",
        "user_id": user_id,
        "apps": composio_payload["apps"],
        "all_connected": composio_payload["all_connected"],
        "onboarding_status": onboarding_status,
        "onboarding_completed_at": completed_at.isoformat() if completed_at else None,
        "route_hint": route_hint,
        "onboarding_session_id": _get_onboarding_session_id(user_id),
        "profile_exists": profile_exists,
    }


@app.post("/api/onboarding/complete")
async def complete_onboarding(
    payload: dict,
    current_user: AuthUser = Depends(get_authenticated_user),
):
    user_id = current_user.user_id
    body = payload if isinstance(payload, dict) else {}
    sanitized, errors = _validate_onboarding_complete_payload(body)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})

    try:
        result = await _complete_onboarding_workflow(
            user_id=user_id,
            wake_time=str(sanitized["wake_time"]),
            bedtime=str(sanitized["bedtime"]),
            timezone_name=(
                str(sanitized["timezone"]) if "timezone" in sanitized else None
            ),
            playbook=sanitized.get("playbook")
            if isinstance(sanitized.get("playbook"), dict)
            else None,
            health_anchors=sanitized.get("health_anchors")
            if isinstance(sanitized.get("health_anchors"), list)
            else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"errors": [str(exc)]}) from exc
    return result


# ========================================
# Cron Endpoints
# ========================================


def _format_calendar_reminder_body(payload: dict, timezone_name: str) -> str:
    event_title = str(payload.get("event_title") or "upcoming event")
    start_raw = payload.get("event_start_time")
    try:
        if isinstance(start_raw, str):
            start_dt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            if start_dt.tzinfo:
                start_dt = start_dt.astimezone(ZoneInfo(timezone_name))
            return f"{event_title} starts at {start_dt.strftime('%I:%M %p')}."
    except Exception:
        pass
    return f"{event_title} starts soon."


async def _resolve_calendar_reminder_timezone(
    event_user_id: str,
    payload: dict,
) -> str | None:
    """Resolve calendar reminder timezone from payload first, then profile."""
    timezone_name = _normalize_timezone(payload.get("timezone"))
    if timezone_name:
        return timezone_name

    profile = await user_repo.get_profile(event_user_id)
    return _normalize_timezone((profile or {}).get("timezone"))


async def _resolve_session_timezone_for_event(
    event_user_id: str,
    payload: dict,
    *,
    calendar_timezone: str | None = None,
) -> str | None:
    if calendar_timezone:
        return calendar_timezone
    payload_timezone = _normalize_timezone(payload.get("timezone"))
    if payload_timezone:
        return payload_timezone

    try:
        profile = await user_repo.get_profile(event_user_id)
    except Exception as profile_error:
        logger.warning(
            "[execute-event] Failed to load profile timezone for user=%s: %s",
            event_user_id,
            profile_error,
        )
        return None
    profile_timezone = _normalize_timezone((profile or {}).get("timezone"))
    return profile_timezone


def _validate_scheduler_secret(
    provided_secret: str | None,
) -> None:
    expected_secret = os.getenv("SCHEDULER_SECRET", "").strip()
    if not expected_secret:
        logger.error("SCHEDULER_SECRET is not configured")
        raise HTTPException(status_code=500, detail="scheduler_secret_not_configured")
    if not provided_secret or provided_secret.strip() != expected_secret:
        raise HTTPException(status_code=401, detail="unauthorized_scheduler_request")


@app.post("/api/execute-event/{event_id}")
async def execute_event(
    event_id: str,
    scheduler_secret: str | None = Header(default=None, alias="X-Scheduler-Secret"),
):
    """
    Execute a specific scheduled event.
    Called by the scheduler at the scheduled time.
    
    Security: Event IDs are UUIDs (unguessable) and execution is idempotent.
    """
    _validate_scheduler_secret(scheduler_secret)

    event = await event_repo.get_by_id(event_id)
    
    # Null check: if event doesn't exist, return 404
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Idempotency check: if already executed, return success
    if _event_field(event, "executed", False):
        logger.info(f"Event {event_id} already executed, skipping")
        return {"status": "already_executed"}
    
    event_type = str(_event_field(event, "event_type", "checkin"))
    event_user_id = _event_field(event, "user_id")
    if not event_user_id:
        raise HTTPException(status_code=500, detail="Event missing user_id")

    payload = _event_field(event, "payload", {}) or {}
    calendar_timezone: str | None = None
    if event_type == "calendar_reminder":
        calendar_timezone = await _resolve_calendar_reminder_timezone(event_user_id, payload)
        if calendar_timezone:
            payload = dict(payload)
            payload["timezone"] = calendar_timezone

    reason = str(payload.get("reason") or "scheduled_checkin")
    session_timezone = await _resolve_session_timezone_for_event(
        event_user_id,
        payload,
        calendar_timezone=calendar_timezone,
    )
    if not session_timezone:
        logger.warning(
            "[execute-event] Missing timezone for user=%s event_id=%s event_type=%s",
            event_user_id,
            event_id,
            event_type,
        )
        session_timezone = None
    session_id = ADKSessionManager.get_daily_session_id(
        event_user_id,
        timezone_name=session_timezone,
    )
    trigger_type = str(payload.get("trigger_type") or event_type)
    calendar_event_id = payload.get("calendar_event_id")
    scheduled_time = _as_datetime(_event_field(event, "scheduled_time"))

    title = "Check-in"
    body = "You have a scheduled check-in."
    if event_type == "morning_wake":
        title = "Good morning"
        body = "Ready to plan your day?"
    elif event_type == "calendar_reminder":
        title = f"Upcoming: {payload.get('event_title') or 'Event'}"
        if calendar_timezone:
            body = _format_calendar_reminder_body(payload, calendar_timezone)
        else:
            body = "Reminder unavailable: missing timezone."
    elif event_type == "checkin":
        title = "Check-in"
        body = f"It is time for your check-in ({reason})."

    notification_data = {
        "session_id": session_id,
        "type": event_type,
        "trigger_type": trigger_type,
        "event_id": event_id,
        "user_id": event_user_id,
        "source": "push",
        "entry_mode": ENTRY_MODE_PROACTIVE,
        "scheduled_time": scheduled_time.isoformat() if scheduled_time else None,
    }
    if isinstance(calendar_event_id, str) and calendar_event_id:
        notification_data["calendar_event_id"] = calendar_event_id

    push_sent = False
    if not session_timezone:
        logger.warning(
            "[execute-event] Skipping push due to missing session timezone user=%s event_id=%s",
            event_user_id,
            event_id,
        )
    elif event_type == "calendar_reminder" and not calendar_timezone:
        logger.warning(
            "[execute-event] Skipping calendar reminder push due to missing timezone user=%s event_id=%s",
            event_user_id,
            event_id,
        )
    else:
        logger.info(
            "[execute-event] Dispatching push user=%s event_id=%s event_type=%s",
            event_user_id,
            event_id,
            event_type,
        )
        push_sent = await send_push_notification(
            event_user_id,
            title,
            body,
            notification_data,
        )

    now_utc = datetime.now(timezone.utc)
    last_error = None if push_sent else "push_failed_or_missing_token"
    if not session_timezone:
        last_error = "missing_timezone"
    elif event_type == "calendar_reminder" and not calendar_timezone:
        last_error = "missing_timezone"

    await event_repo.update_event(
        event_id,
        executed=True,
        last_error=last_error,
        last_attempt_at=now_utc,
    )
    
    # Cleanup: delete the pg_cron job
    event_cron_job_id = _event_field(event, "cron_job_id")
    if event_cron_job_id:
        try:
            await cron_service.delete_job(event_cron_job_id)
        except Exception as cleanup_error:
            logger.warning(f"Failed to cleanup cron job {event_cron_job_id}: {cleanup_error}")
            # Don't fail the request if cleanup fails

    return {
        "status": "executed",
        "event_id": event_id,
        "event_type": event_type,
        "push_sent": push_sent,
    }

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


class AppLifecycle:
    """Minimal lifecycle orchestrator for startup/shutdown tasks."""

    async def startup(self) -> None:
        settings = _read_lifecycle_settings()
        if not settings.enable_reliability_bootstrap:
            logger.info("[bootstrap] Reliability bootstrap disabled by env")
            return

        logger.info("[bootstrap] Automated event reminders enabled")

        try:
            users = await user_repo.get_all_users()
            eligible_users = [user for user in users if _is_onboarding_complete(user)]
            logger.info(
                "[bootstrap] Seeding morning wake events for %s/%s users (onboarding complete)",
                len(eligible_users),
                len(users),
            )
            for user in eligible_users:
                try:
                    await _ensure_morning_wake_for_user(user)
                except Exception as user_error:
                    logger.warning(
                        f"[bootstrap] Failed to seed morning wake for user {(user or {}).get('user_id')}: {user_error}"
                    )
        except Exception as e:
            logger.warning(f"[bootstrap] Failed to seed morning wakes: {e}")

        if settings.enable_morning_cron_reconcile:
            try:
                await _reconcile_missing_cron_jobs()
            except Exception as e:
                logger.warning(f"[bootstrap] Failed cron reconciliation: {e}")

    async def shutdown(self) -> None:
        """Cleanup async resources."""
        try:
            await close_pool()
        except Exception as e:
            logger.warning(f"[shutdown] Failed to close DB pool: {e}")


lifecycle_manager = AppLifecycle()


async def reliability_bootstrap() -> None:
    """Backward-compatible alias for startup bootstrap."""
    await lifecycle_manager.startup()


async def shutdown_cleanup() -> None:
    """Backward-compatible alias for shutdown cleanup."""
    await lifecycle_manager.shutdown()


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
    live_event_count = 0
    seen_function_call_ids: dict[str, str] = {}
    seen_function_response_ids: set[str] = set()
    ws_opened_at = time.monotonic()
    audio_chunk_count = 0
    audio_total_bytes = 0
    audio_zero_chunks = 0
    audio_odd_chunks = 0
    first_audio_chunk_size: int | None = None
    first_audio_sent_at: float | None = None
    onboarding_mode = False
    last_onboarding_state_persist_at = 0.0
    onboarding_state_persist_saved_count = 0
    onboarding_state_persist_skipped_count = 0
    onboarding_completion_signal_sent = False
    onboarding_completion_signal_pending = False
    set_ui_event_queue(ui_event_queue)
    run_config = AgentRuntime.get_realtime_run_config()

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
        try:
            profile = await user_repo.get_profile(user_id)
        except Exception as profile_error:
            logger.warning("[WS-INIT] Failed to load profile for %s: %s", user_id, profile_error)
            profile = None

        all_connected: bool | None = None
        try:
            composio_payload = await _get_composio_status_payload(user_id)
            all_connected = bool(composio_payload.get("all_connected"))
        except Exception as composio_status_error:
            logger.warning(
                "[WS-INIT] Failed to resolve integration status for %s: %s",
                user_id,
                composio_status_error,
            )

        lifecycle_state = _resolve_user_lifecycle_state(
            profile=profile,
            all_connected=all_connected,
        )
        if lifecycle_state == LIFECYCLE_NEEDS_CONNECT_FLOW:
            await websocket.close(code=4403, reason="connect_flow_required")
            return
        onboarding_mode = _use_onboarding_mode(lifecycle_state)

        client_timezone = _normalize_timezone(init_message.get("timezone"))
        profile_timezone = _normalize_timezone((profile or {}).get("timezone"))
        resolved_timezone = client_timezone or profile_timezone

        resume_session_id = init_message.get("resume_session_id")
        requested_session_id = (
            resume_session_id
            if isinstance(resume_session_id, str) and resume_session_id.strip()
            else session_id
        )
        trigger_type = init_message.get("trigger_type")
        entry_context = _build_entry_context(
            init_message,
            default_trigger_type=trigger_type,
            resolved_timezone=resolved_timezone,
        )
        if _is_entry_context_stale(entry_context, resolved_timezone):
            entry_context["is_stale"] = True
            entry_context["entry_mode"] = ENTRY_MODE_REACTIVE
            requested_session_id = None

        if onboarding_mode:
            unified_session_id = _select_onboarding_session_id(user_id, requested_session_id)
        else:
            unified_session_id = _select_ws_session_id(
                user_id,
                requested_session_id,
                timezone_name=resolved_timezone,
            )

        logger.info(
            "[WS-INIT] Authenticated websocket user=%s, session=%s, onboarding_mode=%s, lifecycle_state=%s",
            user_id,
            unified_session_id,
            onboarding_mode,
            lifecycle_state,
        )

        current_user_id.set(user_id)
        current_session_id.set(unified_session_id)

        session = await session_manager.get_or_create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=unified_session_id,
        )

        effective_trigger_type = "onboarding" if onboarding_mode else None
        if isinstance(trigger_type, str) and trigger_type and not onboarding_mode:
            effective_trigger_type = trigger_type
        if effective_trigger_type:
            session.state["trigger_type"] = effective_trigger_type
        session.state["agent_mode"] = "onboarding" if onboarding_mode else "main"
        session.state["lifecycle_state"] = lifecycle_state
        session.state["entry_mode"] = str(entry_context.get("entry_mode") or ENTRY_MODE_REACTIVE)
        session.state["entry_context"] = entry_context

        current_user_timezone.set(resolved_timezone)
        if resolved_timezone:
            session.state["user_timezone"] = resolved_timezone
            if profile is None:
                profile = {"user_id": user_id}
            profile["timezone"] = resolved_timezone
            try:
                await user_repo.update_profile(user_id, timezone=resolved_timezone)
            except Exception as profile_update_error:
                logger.warning(
                    "[WS-INIT] Failed to persist timezone for %s: %s",
                    user_id,
                    profile_update_error,
                )

        if not onboarding_mode:
            profile_context = _build_profile_context(profile)
            session.state["profile_context"] = profile_context

        activation_prompt = (
            "Start onboarding only: welcome the user to Intentive, ask the first onboarding "
            "question, and do not switch to general assistant mode."
            if onboarding_mode
            else _build_main_activation_prompt(entry_context)
        )
        activation_message = types.Content(
            role="user",
            parts=[types.Part(text=activation_prompt)],
        )
        live_request_queue.send_content(activation_message)
        logger.info("[WS-INIT] Sent activation message")

        async def upstream_task() -> None:
            """Receives messages from WebSocket and sends to LiveRequestQueue."""
            logger.debug("upstream_task started")
            nonlocal audio_chunk_count
            nonlocal audio_total_bytes
            nonlocal audio_zero_chunks
            nonlocal audio_odd_chunks
            nonlocal first_audio_chunk_size
            nonlocal first_audio_sent_at
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        logger.info("WebSocket disconnect received in upstream_task")
                        break

                    audio_data = message.get("bytes")
                    if audio_data is not None:
                        chunk_size = len(audio_data)
                        audio_chunk_count += 1
                        audio_total_bytes += chunk_size
                        if first_audio_chunk_size is None:
                            first_audio_chunk_size = chunk_size
                            first_audio_sent_at = time.monotonic()
                        if chunk_size == 0:
                            audio_zero_chunks += 1
                            logger.warning(
                                "[LIVE-DIAG] dropping empty audio chunk idx=%s",
                                audio_chunk_count,
                            )
                            continue
                        if chunk_size % 2 != 0:
                            audio_odd_chunks += 1
                            logger.warning(
                                "[LIVE-DIAG] dropping odd-sized PCM chunk idx=%s size=%s",
                                audio_chunk_count,
                                chunk_size,
                            )
                            continue
                        if audio_chunk_count <= 3:
                            logger.warning(
                                "[LIVE-DIAG] audio_chunk idx=%s size=%s since_ws_open_ms=%s",
                                audio_chunk_count,
                                chunk_size,
                                int((time.monotonic() - ws_opened_at) * 1000),
                            )
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
                        text_payload = json_message.get("text")
                        if not isinstance(text_payload, str):
                            logger.warning(
                                "Ignoring invalid text payload type: %s",
                                type(text_payload).__name__,
                            )
                            continue
                        text_payload = text_payload.strip()
                        if not text_payload:
                            continue
                        content = types.Content(
                            role="user",
                            parts=[types.Part(text=text_payload)],
                        )
                        live_request_queue.send_content(content)
            except Exception as e:
                logger.debug("upstream_task ended: %s", e)

        async def _process_downstream_event(event) -> bool:
            """Process one ADK event and forward to frontend. Returns False on closed socket."""
            nonlocal live_event_count
            nonlocal last_onboarding_state_persist_at
            nonlocal onboarding_state_persist_saved_count
            nonlocal onboarding_state_persist_skipped_count
            nonlocal onboarding_completion_signal_sent
            nonlocal onboarding_completion_signal_pending
            live_event_count += 1
            has_audio_inline_part = False
            has_text_part = False
            has_function_part = False
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
                    if getattr(part, "text", None):
                        has_text_part = True
                    part_attrs = [
                        a
                        for a in ["text", "function_call", "function_response", "inline_data"]
                        if getattr(part, a, None) is not None
                    ]
                    if part_attrs:
                        logger.info("[MAIN-EVENT] Part %s has: %s", i, part_attrs)

                    func_call = getattr(part, "function_call", None)
                    if func_call is not None:
                        has_function_part = True
                        call_id = getattr(func_call, "id", None)
                        call_name = getattr(func_call, "name", "unknown")
                        call_args = getattr(func_call, "args", None)
                        arg_keys = sorted(call_args.keys()) if isinstance(call_args, dict) else []
                        logger.info(
                            "[LIVE-DIAG] function_call name=%s id=%s arg_keys=%s",
                            call_name,
                            call_id,
                            arg_keys,
                        )
                        if isinstance(call_id, str) and call_id:
                            seen_function_call_ids[call_id] = call_name
                        else:
                            logger.warning(
                                "[LIVE-DIAG] function_call missing id name=%s",
                                call_name,
                            )

                    func_resp = getattr(part, "function_response", None)
                    inline_data = getattr(part, "inline_data", None)
                    if inline_data is not None:
                        inline_mime_type = getattr(inline_data, "mime_type", None)
                        if isinstance(inline_data, dict) and inline_mime_type is None:
                            inline_mime_type = inline_data.get("mime_type")
                        if isinstance(inline_mime_type, str) and inline_mime_type.startswith(
                            "audio/"
                        ):
                            has_audio_inline_part = True
                    if func_resp is None:
                        continue
                    has_function_part = True
                    func_name = getattr(func_resp, "name", "unknown")
                    func_resp_id = getattr(func_resp, "id", None)
                    response_data = getattr(func_resp, "response", None)
                    response_keys = (
                        sorted(response_data.keys()) if isinstance(response_data, dict) else []
                    )
                    logger.info(
                        "[LIVE-DIAG] function_response name=%s id=%s response_keys=%s",
                        func_name,
                        func_resp_id,
                        response_keys,
                    )
                    if isinstance(func_resp_id, str) and func_resp_id:
                        seen_function_response_ids.add(func_resp_id)
                        if func_resp_id in seen_function_call_ids:
                            logger.info(
                                "[LIVE-DIAG] function_response matched_call id=%s call_name=%s response_name=%s",
                                func_resp_id,
                                seen_function_call_ids[func_resp_id],
                                func_name,
                            )
                        else:
                            logger.warning(
                                "[LIVE-DIAG] function_response unmatched id=%s response_name=%s known_call_ids=%s",
                                func_resp_id,
                                func_name,
                                sorted(seen_function_call_ids.keys()),
                            )
                    else:
                        logger.warning(
                            "[LIVE-DIAG] function_response missing id name=%s",
                            func_name,
                        )
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
                    if (
                        onboarding_mode
                        and not onboarding_completion_signal_sent
                        and func_name == "complete_onboarding"
                        and isinstance(response_data, dict)
                    ):
                        completion_payload = response_data.get("result")
                        if not isinstance(completion_payload, dict):
                            completion_payload = response_data
                        completion_status = str(completion_payload.get("status") or "").lower()
                        completion_onboarding_status = str(
                            completion_payload.get("onboarding_status") or ""
                        ).lower()
                        completion_route_hint = str(completion_payload.get("route_hint") or "")
                        completion_success = (
                            completion_status in {"ok", "partial_success"}
                            and completion_onboarding_status == ONBOARDING_STATUS_COMPLETED
                            and completion_route_hint == "assistant"
                        )
                        if completion_success:
                            onboarding_completion_signal_pending = True
                            logger.info(
                                "[onboarding] completion signal pending user=%s session=%s",
                                user_id,
                                unified_session_id,
                            )
                        elif completion_status == "error":
                            raw_missing_fields = completion_payload.get("missing_fields")
                            missing_fields = (
                                [field for field in raw_missing_fields if isinstance(field, str)]
                                if isinstance(raw_missing_fields, list)
                                else []
                            )
                            failure_message = str(
                                completion_payload.get("error")
                                or completion_payload.get("message")
                                or "Onboarding could not be completed yet."
                            )
                            onboarding_failure_event = {
                                "type": "onboarding_completion_failed",
                                "message": failure_message,
                                "missing_fields": missing_fields,
                            }
                            try:
                                await websocket.send_text(json.dumps(onboarding_failure_event))
                                logger.info(
                                    "[onboarding] completion failed signal emitted user=%s session=%s missing_fields=%s",
                                    user_id,
                                    unified_session_id,
                                    missing_fields,
                                )
                            except (RuntimeError, WebSocketDisconnect):
                                logger.info(
                                    "WebSocket closed while sending onboarding completion failure event"
                                )

            event_json = event.model_dump_json(exclude_none=True, by_alias=True)
            try:
                await websocket.send_text(event_json)
            except (RuntimeError, WebSocketDisconnect):
                logger.info("WebSocket connection closed, stopping downstream_task")
                return False

            payload_turn_complete = False
            event_payload = getattr(event, "_payload", None)
            if isinstance(event_payload, dict):
                payload_turn_complete = bool(
                    event_payload.get("turnComplete")
                    or event_payload.get("turn_complete")
                )
            has_turn_complete = bool(
                getattr(event, "turnComplete", False)
                or getattr(event, "turn_complete", False)
                or payload_turn_complete
            )
            if (
                onboarding_mode
                and onboarding_completion_signal_pending
                and not onboarding_completion_signal_sent
                and has_turn_complete
            ):
                onboarding_completion_signal_pending = False
                onboarding_completion_signal_sent = True
                onboarding_complete_event = {
                    "type": "onboarding_completed",
                    "next_action": "show_done_screen",
                    "route_hint": "assistant",
                }
                try:
                    await websocket.send_text(json.dumps(onboarding_complete_event))
                    logger.info(
                        "[onboarding] completion signal emitted user=%s session=%s",
                        user_id,
                        unified_session_id,
                    )
                except (RuntimeError, WebSocketDisconnect):
                    logger.info(
                        "WebSocket closed while sending onboarding completion event"
                    )

            should_persist_state = True
            now_mono = time.monotonic()
            has_interrupted = bool(getattr(event, "interrupted", False))
            has_server_transcription = bool(
                getattr(getattr(event, "server_content", None), "input_transcription", None)
                or getattr(getattr(event, "server_content", None), "output_transcription", None)
            )
            force_persist = (
                has_turn_complete
                or has_interrupted
                or has_server_transcription
                or has_function_part
                or has_text_part
            )
            if (
                has_audio_inline_part
                and not force_persist
                and now_mono - last_onboarding_state_persist_at
                < ONBOARDING_STATE_PERSIST_INTERVAL_SECONDS
            ):
                should_persist_state = False
                onboarding_state_persist_skipped_count += 1
            else:
                last_onboarding_state_persist_at = now_mono

            if not should_persist_state:
                return True

            try:
                assert unified_session_id is not None
                assert user_id is not None
                await session_manager.save_agent_session_to_db(
                    unified_session_id,
                    session.state,
                    user_id=user_id,
                )
                onboarding_state_persist_saved_count += 1
            except Exception as e:
                logger.warning("Failed to persist session state: %s", e)

            return True

        async def downstream_task() -> None:
            """Receives Events from run_live() and sends to WebSocket."""
            logger.debug("downstream_task started")
            max_retries = 2
            selected_runner = onboarding_runner if onboarding_mode else main_runner
            selected_agent = onboarding_agent if onboarding_mode else main_agent
            model_name = str(selected_agent.model)
            if user_id and unified_session_id:
                _log_live_setup_diagnostics(
                    selected_agent=selected_agent,
                    run_config=run_config,
                    onboarding_mode=onboarding_mode,
                    user_id=user_id,
                    session_id=unified_session_id,
                )
            logger.info("[LIVE] Attempting run_live with conversation model: %s", model_name)

            for attempt in range(1, max_retries + 1):
                try:
                    assert user_id is not None
                    assert unified_session_id is not None
                    async for event in selected_runner.run_live(
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
                    if "request contains an invalid argument" in str(e).lower():
                        unmatched_response_ids = sorted(
                            seen_function_response_ids - set(seen_function_call_ids.keys())
                        )
                        logger.error(
                            "[LIVE] Gemini Live rejected request as invalid argument. "
                            "Verify model id and request payload schema. model=%s error=%s",
                            model_name,
                            e,
                        )
                        logger.error(
                            "[LIVE-DIAG] invalid-argument context events_seen=%s call_ids=%s "
                            "response_ids=%s unmatched_response_ids=%s audio_chunks=%s "
                            "audio_total_bytes=%s audio_zero_chunks=%s audio_odd_chunks=%s "
                            "first_audio_chunk_size=%s first_audio_after_ws_open_ms=%s",
                            live_event_count,
                            sorted(seen_function_call_ids.keys()),
                            sorted(seen_function_response_ids),
                            unmatched_response_ids,
                            audio_chunk_count,
                            audio_total_bytes,
                            audio_zero_chunks,
                            audio_odd_chunks,
                            first_audio_chunk_size,
                            (
                                int((first_audio_sent_at - ws_opened_at) * 1000)
                                if first_audio_sent_at is not None
                                else None
                            ),
                        )
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
        logger.warning(
            "[LIVE-DIAG] state_persist mode=%s saved=%s skipped=%s interval_s=%s",
            "onboarding" if onboarding_mode else "main",
            onboarding_state_persist_saved_count,
            onboarding_state_persist_skipped_count,
            ONBOARDING_STATE_PERSIST_INTERVAL_SECONDS,
        )
        logger.info("Closing live_request_queue")
        live_request_queue.close()
        set_ui_event_queue(None)

        if session is not None and user_id:
            logger.info("[POST-CONVERSATION] Session closed for user=%s", user_id)


# ========================================
# Main Entry Point
# ========================================

if __name__ == "__main__":
    import uvicorn
    # Get host and port from environment variables with defaults
    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port_raw = os.getenv("PORT")
    if not port_raw:
        raise RuntimeError("PORT environment variable not set")
    port = int(port_raw)
    
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
