from notification_service import send_push_notification
from context import current_user_id
import logging

logger = logging.getLogger(__name__)

async def send_push_notification_tool(title: str, body: str) -> str:
    """
    Sends a push notification to the user's device.
    Use this when you need to alert the user about something important (e.g., a timer ending).
    
    Args:
        title: The title of the notification (short and punchy).
        body: The main content of the notification.
    """
    user_id = current_user_id.get()
    if not user_id:
        return "Error: No current user context found."
        
    logger.info(f"Agent executing send_push_notification_tool: {title} - {body}")
    
    # We include a 'type': 'notification' in data so the frontend can handle it genericly if needed
    await send_push_notification(user_id, title, body, data={"type": "agent_alert"})
    
    return f"Notification sent: {title}"
