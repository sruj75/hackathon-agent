"""
Timer tools for background scheduling.
Used only in Thinking Mode to schedule future interventions.
"""
from datetime import datetime, timedelta
import pytz
from context import current_user_id, current_session_id
from repos import event_repo
import cron_service
from .composio_tools import _get_user_timezone
import logging

logger = logging.getLogger(__name__)

async def set_checkin_timer(
    duration_minutes: int,
    reason: str
) -> str:
    """
    Schedule a future check-in by creating a scheduled event.
    
    Use this to plan your next intervention after analyzing the situation.
    
    Args:
        duration_minutes: How many minutes from now to schedule the check-in
        reason: Brief description of why this check-in is happening (e.g., "end_of_deep_work", "post_lunch")
    
    Returns:
        Confirmation message with scheduled time
    
    Example:
        set_checkin_timer(90, "end_of_deep_work_block")
        → "Timer set for 2:30 PM (in 90 minutes)"
    """
    user_id = current_user_id.get()
    session_id = current_session_id.get()
    
    if not user_id:
        logger.error("[set_checkin_timer] Missing context (user_id)")
        return "Error: Cannot set timer - missing context."
    
    try:
        # Calculate scheduled time in user's timezone
        tz_name = _get_user_timezone()
        tz = pytz.timezone(tz_name)
        now = datetime.now(tz)
        scheduled_time = now + timedelta(minutes=duration_minutes)
        
        # Create event in database
        event = await event_repo.create_event(
            user_id,
            scheduled_time,
            "checkin",
            payload={"reason": reason}
        )
        
        # Create corresponding dynamic cron job
        cron_job_id = await cron_service.create_one_time_job(
            target_datetime=scheduled_time,
            event_id=event.id,
            timezone=tz_name
        )
        
        # Update event with cron job ID
        await event_repo.update_cron_job_id(event.id, cron_job_id)
        
        logger.info(
            f"[set_checkin_timer] Scheduled check-in for {user_id} at {scheduled_time.strftime('%I:%M %p')} "
            f"(reason: {reason}, cron_job: {cron_job_id})"
        )
        
        return (
            f"✅ Timer set for {scheduled_time.strftime('%I:%M %p')} "
            f"(in {duration_minutes} minutes). Reason: {reason}"
        )
        
    except Exception as e:
        logger.error(f"[set_checkin_timer] Failed to set timer: {e}", exc_info=True)
        return f"⚠️ Failed to set timer: {str(e)}"
