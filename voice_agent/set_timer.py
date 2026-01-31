from datetime import datetime, timedelta
import pytz
from .composio_tools import _get_user_timezone
from context import current_user_id
from repos import event_repo
from database import SessionLocal
import logging

logger = logging.getLogger(__name__)

async def set_checkin_timer(duration_minutes: int, notification_body: str, notification_title: str = "Check-in") -> dict:
    """
    Sets a timer for a check-in with a pre-generated message.
    
    Args:
        duration_minutes: Number of minutes to wait
        notification_body: The actual message to send (e.g. "How is the deep work going?")
        notification_title: The title of the notification (default: "Check-in")
        
    Returns:
        Dict with status and scheduled time
    """
    try:
        # Get user timezone
        tz_name = _get_user_timezone()
        tz = pytz.timezone(tz_name)
        
        # Calculate scheduled time
        # We use the user's timezone to calculate "now", but for storage/comparison
        # it is often safer to normalize to UTC. However, following the spec,
        # we calculate the target time in the user's timezone.
        scheduled_time = datetime.now(tz) + timedelta(minutes=duration_minutes)
        
        # Get user_id from context
        user_id = current_user_id.get()
        
        # Save to DB
        async with SessionLocal() as db:
            # We convert to naive UTC for storage consistency if needed, 
            # or rely on the repo/DB to handle the aware object.
            # For SQLite+SQLAlchemy, it's best to store as naive UTC or ISO string.
            # Let's convert to UTC to be safe for backend comparisons.
            scheduled_time_utc = scheduled_time.astimezone(pytz.UTC)
            
            # Using the naive UTC time for the DB to avoid timezone confusion in SQL
            # (making it naive removes the +00:00 offset info but keeps the UTC time value)
            scheduled_time_db = scheduled_time_utc.replace(tzinfo=None)
            
            await event_repo.create_event(
                db=db,
                user_id=user_id,
                scheduled_time=scheduled_time_db,
                event_type="checkin",
                payload={
                    "title": notification_title,
                    "body": notification_body
                }
            )
            
        return {
            "status": "scheduled", 
            "time": scheduled_time.isoformat(),
            "message": f"Timer set for {duration_minutes} minutes. Will say: '{notification_body}'"
        }
        
    except Exception as e:
        logger.error(f"Error setting timer: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Failed to set timer: {str(e)}"
        }
