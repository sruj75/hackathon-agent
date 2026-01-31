import logging
import json

logger = logging.getLogger(__name__)

async def send_push_notification(user_id: str, title: str, body: str, data: dict = None):
    """
    Stub for sending push notifications via Expo.
    In v0, this just logs the notification to stdout.
    """
    if data is None:
        data = {}
        
    notification_payload = {
        "to": user_id,
        "title": title,
        "body": body,
        "data": data
    }
    
    # Log with a specific prefix so we can grep it in tests
    logger.info(f"🚀 [PUSH NOTIFICATION] Sending to {user_id}: {json.dumps(notification_payload)}")
    
    # TODO: Phase 6 - Integrate actual Expo Push API here
    return True
