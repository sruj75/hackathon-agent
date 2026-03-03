from __future__ import annotations

import json
import logging
from typing import Any

from context import current_user_id, current_user_timezone
from onboarding_service import (
    ONBOARDING_STATUS_COMPLETED,
    get_onboarding_context_for_user,
    save_onboarding_progress_for_user,
    validate_playbook_for_completion,
)

logger = logging.getLogger(__name__)


async def get_onboarding_context() -> dict[str, Any]:
    """Return existing onboarding context for resuming incomplete sessions."""
    try:
        user_id = current_user_id.get()
    except LookupError:
        return {"status": "error", "error": "missing_user_context"}

    if not user_id:
        return {"status": "error", "error": "missing_user_context"}

    try:
        return await get_onboarding_context_for_user(user_id)
    except Exception as exc:
        logger.exception("Failed to read onboarding context for user=%s", user_id)
        return {"status": "error", "error": str(exc)}


async def complete_onboarding(
    wake_time: str,
    bedtime: str,
    playbook_json: str = "{}",
) -> dict[str, Any]:
    """
    Persist onboarding completion artifact.

    Timezone is sourced from device context when available.
    """
    try:
        user_id = current_user_id.get()
    except LookupError:
        return {"status": "error", "error": "missing_user_context"}

    if not user_id:
        return {"status": "error", "error": "missing_user_context"}

    try:
        timezone_name = current_user_timezone.get()
    except LookupError:
        timezone_name = None
    try:
        parsed = json.loads(playbook_json) if playbook_json else {}
    except json.JSONDecodeError:
        return {"status": "error", "error": "playbook_json must be valid JSON"}

    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        return {"status": "error", "error": "playbook_json must decode to an object"}
    playbook = parsed

    playbook_errors = validate_playbook_for_completion(playbook)
    if playbook_errors:
        return {"status": "error", "error": "; ".join(playbook_errors)}

    logger.info("[onboarding-tool] complete_onboarding start user=%s", user_id)
    try:
        import main as app_main  # Lazy import to avoid import cycles at module load.

        result = await app_main._complete_onboarding_workflow(  # type: ignore[attr-defined]
            user_id=user_id,
            wake_time=wake_time,
            bedtime=bedtime,
            timezone_name=timezone_name,
            playbook=playbook,
            health_anchors=None,
        )
        response = {
            "status": result.get("status"),
            "onboarding_status": result.get("onboarding_status"),
            "onboarding_completed_at": result.get("onboarding_completed_at"),
            "route_hint": result.get("route_hint"),
            "handoff_to_main": bool(result.get("handoff_to_main")),
            "scheduler": result.get("scheduler"),
            "message": "Onboarding completed successfully.",
        }
        status = str(response.get("status") or "")
        onboarding_status = str(response.get("onboarding_status") or "")
        logger.info(
            "[onboarding-tool] complete_onboarding result user=%s status=%s onboarding_status=%s route_hint=%s",
            user_id,
            status,
            onboarding_status,
            response.get("route_hint"),
        )
        if onboarding_status != ONBOARDING_STATUS_COMPLETED:
            logger.warning(
                "[onboarding-tool] completion did not mark completed user=%s result=%s",
                user_id,
                response,
            )
        return response
    except ValueError as exc:
        logger.warning("[onboarding-tool] complete_onboarding validation_error user=%s error=%s", user_id, exc)
        return {"status": "error", "error": str(exc)}
    except Exception as exc:
        logger.exception("Failed to complete onboarding for user=%s", user_id)
        return {"status": "error", "error": str(exc)}


async def save_onboarding_progress(
    wake_time: str | None = None,
    bedtime: str | None = None,
    timezone: str | None = None,
    summary: str | None = None,
    struggles: list[str] | None = None,
    goals: list[str] | None = None,
    communication_style: str | None = None,
) -> dict[str, Any]:
    """Persist partial onboarding progress while keeping onboarding status pending."""
    try:
        user_id = current_user_id.get()
    except LookupError:
        return {"status": "error", "error": "missing_user_context"}

    if not user_id:
        return {"status": "error", "error": "missing_user_context"}

    try:
        context_payload = await save_onboarding_progress_for_user(
            user_id,
            wake_time=wake_time,
            bedtime=bedtime,
            timezone_name=timezone,
            summary=summary,
            struggles=struggles if isinstance(struggles, list) else None,
            goals=goals if isinstance(goals, list) else None,
            communication_style=communication_style,
        )
        logger.info(
            "[onboarding-tool] save_onboarding_progress user=%s wake=%s bedtime=%s timezone=%s "
            "summary=%s struggles=%s goals=%s style=%s",
            user_id,
            bool(wake_time),
            bool(bedtime),
            bool(timezone),
            bool(summary),
            bool(struggles),
            bool(goals),
            bool(communication_style),
        )
        return {
            "status": "ok",
            "message": "Onboarding progress saved.",
            "context": context_payload.get("context"),
        }
    except ValueError as exc:
        logger.warning(
            "[onboarding-tool] save_onboarding_progress validation_error user=%s error=%s",
            user_id,
            exc,
        )
        return {"status": "error", "error": str(exc)}
    except Exception as exc:
        logger.exception("Failed to save onboarding progress for user=%s", user_id)
        return {"status": "error", "error": str(exc)}
