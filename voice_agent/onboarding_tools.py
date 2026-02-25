from __future__ import annotations

import logging
from typing import Any

from context import current_user_id, current_user_timezone
from onboarding_service import (
    complete_onboarding_for_user,
    get_onboarding_context_for_user,
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
    playbook: dict[str, Any] | None = None,
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

    timezone_name = current_user_timezone.get()

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
            "status": result["status"],
            "onboarding_status": result["onboarding_status"],
            "onboarding_completed_at": result["onboarding_completed_at"],
            "route_hint": result["route_hint"],
            "scheduler": result["scheduler"],
            "message": "Onboarding completed successfully.",
        }
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    except Exception as exc:
        logger.exception("Failed to complete onboarding for user=%s", user_id)
        return {"status": "error", "error": str(exc)}
