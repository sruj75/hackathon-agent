from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from repos import user_repo

ONBOARDING_STATUS_PENDING = "pending"
ONBOARDING_STATUS_COMPLETED = "completed"
HHMM_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _is_valid_hhmm(value: str | None) -> bool:
    return bool(value and HHMM_PATTERN.match(value))


def _normalize_timezone(timezone_name: str | None) -> str | None:
    if not timezone_name:
        return None
    try:
        ZoneInfo(timezone_name)
        return timezone_name
    except Exception:
        return None


def _normalize_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str):
            clean = item.strip()
            if clean:
                normalized.append(clean)
    return normalized


def _normalize_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _normalize_playbook(
    playbook: dict[str, Any] | None,
    *,
    wake_time: str,
    bedtime: str,
    timezone_name: str,
    completion_time: datetime,
) -> dict[str, Any]:
    source = playbook if isinstance(playbook, dict) else {}
    summary = source.get("summary")
    communication_style = source.get("communication_style")
    schema_version = source.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        schema_version = "1.0"

    return {
        "schema_version": schema_version,
        "created_at": source.get("created_at") or completion_time.isoformat(),
        "updated_at": completion_time.isoformat(),
        "summary": summary if isinstance(summary, str) else "",
        "profile": {
            "wake_time": wake_time,
            "bedtime": bedtime,
            "timezone": timezone_name,
        },
        "struggles": _normalize_str_list(source.get("struggles")),
        "goals": _normalize_str_list(source.get("goals")),
        "communication_style": communication_style,
    }


def validate_playbook_for_completion(playbook: dict[str, Any] | None) -> list[str]:
    """Enforce minimum onboarding intake quality before completion."""
    source = playbook if isinstance(playbook, dict) else {}
    errors: list[str] = []

    summary = source.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("playbook.summary is required")

    struggles = _normalize_str_list(source.get("struggles"))
    if len(struggles) < 1:
        errors.append("playbook.struggles must contain at least one item")

    goals = _normalize_str_list(source.get("goals"))
    if len(goals) < 1:
        errors.append("playbook.goals must contain at least one item")

    communication_style = source.get("communication_style")
    if not isinstance(communication_style, str) or not communication_style.strip():
        errors.append("playbook.communication_style is required")

    return errors


async def get_onboarding_context_for_user(user_id: str) -> dict[str, Any]:
    profile = await user_repo.get_profile(user_id)
    if not profile:
        return {
            "status": "not_found",
            "user_id": user_id,
            "context": None,
        }

    onboarding_status = str(
        profile.get("onboarding_status") or ONBOARDING_STATUS_PENDING
    ).lower()
    if onboarding_status != ONBOARDING_STATUS_COMPLETED:
        onboarding_status = ONBOARDING_STATUS_PENDING

    return {
        "status": "ok",
        "user_id": user_id,
        "context": {
            "onboarding_status": onboarding_status,
            "onboarding_completed_at": profile.get("onboarding_completed_at"),
            "playbook": profile.get("playbook") or {},
            "preferences": {
                "wake_time": profile.get("wake_time"),
                "bedtime": profile.get("bedtime"),
                "timezone": profile.get("timezone"),
                "health_anchors": profile.get("health_anchors") or [],
            },
        },
    }


async def save_onboarding_progress_for_user(
    user_id: str,
    *,
    wake_time: str | None = None,
    bedtime: str | None = None,
    timezone_name: str | None = None,
    summary: str | None = None,
    struggles: list[str] | None = None,
    goals: list[str] | None = None,
    communication_style: str | None = None,
) -> dict[str, Any]:
    profile = await user_repo.get_profile(user_id) or {}
    updates: dict[str, Any] = {}
    existing_status = str(profile.get("onboarding_status") or "").strip().lower()
    already_completed = (
        existing_status == ONBOARDING_STATUS_COMPLETED
        or profile.get("onboarding_completed_at") is not None
    )

    if wake_time is not None:
        wake_time_clean = wake_time.strip()
        if wake_time_clean and not _is_valid_hhmm(wake_time_clean):
            raise ValueError("wake_time must be HH:MM in 24-hour format")
        if wake_time_clean:
            updates["wake_time"] = wake_time_clean

    if bedtime is not None:
        bedtime_clean = bedtime.strip()
        if bedtime_clean and not _is_valid_hhmm(bedtime_clean):
            raise ValueError("bedtime must be HH:MM in 24-hour format")
        if bedtime_clean:
            updates["bedtime"] = bedtime_clean

    if timezone_name is not None:
        resolved_timezone = _normalize_timezone(timezone_name)
        if not resolved_timezone:
            raise ValueError("timezone must be a valid IANA timezone")
        updates["timezone"] = resolved_timezone

    current_playbook = profile.get("playbook")
    merged_playbook = dict(current_playbook) if isinstance(current_playbook, dict) else {}
    now = datetime.now(timezone.utc)
    schema_version = merged_playbook.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        schema_version = "1.0"
    merged_playbook["schema_version"] = schema_version
    merged_playbook["created_at"] = merged_playbook.get("created_at") or now.isoformat()
    merged_playbook["updated_at"] = now.isoformat()

    normalized_summary = _normalize_optional_text(summary)
    if normalized_summary is not None:
        merged_playbook["summary"] = normalized_summary

    if struggles is not None:
        normalized_struggles = _normalize_str_list(struggles)
        if normalized_struggles:
            merged_playbook["struggles"] = normalized_struggles

    if goals is not None:
        normalized_goals = _normalize_str_list(goals)
        if normalized_goals:
            merged_playbook["goals"] = normalized_goals

    normalized_style = _normalize_optional_text(communication_style)
    if normalized_style is not None:
        merged_playbook["communication_style"] = normalized_style

    updates["playbook"] = merged_playbook
    if not already_completed:
        updates["onboarding_status"] = ONBOARDING_STATUS_PENDING
        updates["onboarding_completed_at"] = None

    await user_repo.update_profile(user_id, **updates)
    return await get_onboarding_context_for_user(user_id)


async def complete_onboarding_for_user(
    user_id: str,
    *,
    wake_time: str,
    bedtime: str,
    timezone_name: str | None,
    playbook: dict[str, Any] | None,
    health_anchors: list[str] | None = None,
) -> tuple[dict[str, Any], datetime]:
    if not _is_valid_hhmm(wake_time):
        raise ValueError("wake_time must be HH:MM in 24-hour format")
    if not _is_valid_hhmm(bedtime):
        raise ValueError("bedtime must be HH:MM in 24-hour format")

    playbook_errors = validate_playbook_for_completion(playbook)
    if playbook_errors:
        raise ValueError("; ".join(playbook_errors))

    profile = await user_repo.get_profile(user_id)
    resolved_timezone = _normalize_timezone(timezone_name) or _normalize_timezone(
        (profile or {}).get("timezone")
    )
    if not resolved_timezone:
        raise ValueError("timezone is required and must be a valid IANA timezone")

    completion_time = datetime.now(timezone.utc)
    normalized_playbook = _normalize_playbook(
        playbook,
        wake_time=wake_time,
        bedtime=bedtime,
        timezone_name=resolved_timezone,
        completion_time=completion_time,
    )

    update_payload: dict[str, Any] = {
        "wake_time": wake_time,
        "bedtime": bedtime,
        "timezone": resolved_timezone,
        "playbook": normalized_playbook,
        "onboarding_status": ONBOARDING_STATUS_COMPLETED,
        "onboarding_completed_at": completion_time,
    }
    if health_anchors is not None:
        update_payload["health_anchors"] = health_anchors

    updated_profile = await user_repo.update_profile(user_id, **update_payload)
    return updated_profile, completion_time
