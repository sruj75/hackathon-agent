from datetime import datetime
import pytz
from .composio_tools import _get_user_timezone
from context import current_user_id
from repos import user_repo
from database import SessionLocal
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
        # Fallback to UTC
        now = datetime.now(pytz.UTC)
        return {
            "iso": now.isoformat(),
            "readable": now.strftime("%-I:%M %p"),
            "timezone": "UTC",
            "error": str(e)
        }

async def get_user_preferences() -> dict:
    """
    Get the current user's preferences (wake time, bedtime, health anchors).
    
    Returns:
        dict: {
            "wake_time": "07:00",
            "bedtime": "22:00",
            "timezone": "...",
            "health_anchors": [...]
        }
    """
    try:
        user_id = current_user_id.get()
        
        async with SessionLocal() as db:
            profile = await user_repo.get_profile(db, user_id)
            
            if not profile:
                # Return defaults if no profile exists yet
                logger.info(f"No profile found for user {user_id}, returning defaults")
                return {
                    "wake_time": "07:00",
                    "bedtime": "22:00",
                    "timezone": _get_user_timezone(),
                    "health_anchors": []
                }
            
            return {
                "wake_time": profile.wake_time,
                "bedtime": profile.bedtime,
                "timezone": profile.timezone,
                "health_anchors": profile.health_anchors or []
            }
            
    except Exception as e:
        logger.error(f"Error getting user preferences: {e}", exc_info=True)
        return {
            "error": str(e)
        }
