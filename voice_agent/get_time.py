from datetime import datetime
import pytz
from .composio_tools import _get_user_timezone
from context import current_user_id
from repos import user_repo
import logging

logger = logging.getLogger(__name__)

def get_current_time() -> dict:
    """
    Get the current time in the user's timezone.
    
    Returns:
        dict: {
            "iso": "2024-01-29T14:30:00+05:30",
            "readable": "2:30 PM",
            "timezone": "Asia/Kolkata"
        }
    """
    try:
        tz_name = _get_user_timezone()
        tz = pytz.timezone(tz_name)
        now = datetime.now(tz)
        
        return {
            "iso": now.isoformat(),
            "readable": now.strftime("%-I:%M %p"),
            "timezone": tz_name
        }
    except Exception as e:
        logger.error(f"Error getting current time: {e}", exc_info=True)
        return {
            "iso": None,
            "readable": None,
            "timezone": None,
            "error": str(e)
        }

async def get_user_preferences() -> dict:
    """
    Get the current user's preferences (wake time, bedtime, health anchors).
    
    Returns:
        dict: {
            "wake_time": "HH:MM",
            "bedtime": "HH:MM",
            "timezone": "IANA/Timezone",
            "health_anchors": [...]
        }
    """
    try:
        try:
            user_id = current_user_id.get()
        except LookupError:
            return {"error": "missing_user_context"}
        
        profile = await user_repo.get_profile(user_id)
        
        if not profile:
            logger.warning(f"No profile found for user {user_id}")
            return {
                "error": "profile_not_found",
                "user_id": user_id,
            }

        required_fields = ["wake_time", "bedtime", "timezone"]
        missing_fields = [field for field in required_fields if not profile.get(field)]
        if missing_fields:
            logger.warning(
                f"Profile for user {user_id} is missing required fields: {missing_fields}"
            )
            return {
                "error": "incomplete_profile",
                "user_id": user_id,
                "missing_fields": missing_fields,
            }
        
        return {
            "wake_time": profile.get("wake_time"),
            "bedtime": profile.get("bedtime"),
            "timezone": profile.get("timezone"),
            "health_anchors": profile.get("health_anchors", []) or []
        }
        
    except Exception as e:
        logger.error(f"Error getting user preferences: {e}", exc_info=True)
        return {
            "error": str(e)
        }
