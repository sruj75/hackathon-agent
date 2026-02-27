from __future__ import annotations

import json
import logging
from typing import Any

from context import current_user_id, current_user_timezone
from onboarding_service import (
    complete_onboarding_for_user,
    get_onboarding_context_for_user,
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
        return {
            "status": result.get("status"),
            "onboarding_status": result.get("onboarding_status"),
            "onboarding_completed_at": result.get("onboarding_completed_at"),
            "route_hint": result.get("route_hint"),
            "handoff_to_main": bool(result.get("handoff_to_main")),
            "scheduler": result.get("scheduler"),
            "message": "Onboarding completed successfully.",
        }
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:
        logger.exception("Failed to complete onboarding for user=%s", user_id)
        return {"status": "error", "error": str(exc)}
