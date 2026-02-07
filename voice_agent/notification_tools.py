from notification_service import send_push_notification
from context import current_user_id, current_session_id
import logging

logger = logging.getLogger(__name__)

async def send_push_notification_tool(
    title: str,
    body: str,
    notification_type: str = "checkin"
) -> str:
    """
    Send a push notification to the user's device.

    Use this when you want to invite the user to join a conversation.
    The notification will include session context so the conversation can resume.

    Args:
        title: Notification title (e.g., "Check-in", "Good Morning")
        body: Notification message (e.g., "How's deep work going?")
        notification_type: Type of notification (e.g., "checkin", "morning_wake")

    Returns:
        Success message or error description
    """
    user_id = current_user_id.get()
    session_id = current_session_id.get()
    
    if not user_id:
        logger.error("[send_push_notification_tool] No current user context found")
        return "Error: No current user context found."
    
    if not session_id:
        logger.warning("[send_push_notification_tool] No session_id found - notification will not support conversation resume")
        
    logger.info(
        f"[send_push_notification_tool] Agent sending notification: "
        f"title='{title}', body='{body}', type='{notification_type}'"
    )
    
    # Data payload for deep linking
    data = {
        "session_id": session_id,
        "type": notification_type,
        "user_id": user_id
    }
    
    success = await send_push_notification(user_id, title, body, data)
    
    if success:
        return f"✅ Notification sent: {title}"
    else:
        return f"⚠️ Failed to send notification (user may not have push enabled)"
