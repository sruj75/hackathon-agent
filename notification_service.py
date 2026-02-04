import logging
import json
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from repos import user_repo

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"

async def send_push_notification(
    db: AsyncSession,
    user_id: str,
    title: str,
    body: str,
    data: dict = None
) -> bool:
    """
    Send push notification to user's device via Expo Push API.
    
    Args:
        db: Database session
        user_id: User identifier
        title: Notification title
        body: Notification message
        data: Additional data payload (e.g., session_id, type)
    
    Returns:
        True if notification sent successfully, False otherwise
    """
    if data is None:
        data = {}
    
    try:
        # 1. Get user's Expo push token from DB
        push_token = await user_repo.get_push_token(db, user_id)
        
        if not push_token:
            logger.warning(f"[PUSH NOTIFICATION] No push token found for user {user_id}")
            return False
        
        # 2. Build notification payload
        notification_payload = {
            "to": push_token,
            "title": title,
            "body": body,
            "data": data,
            "sound": "default",
            "priority": "high",
        }
        
        logger.info(
            f"🚀 [PUSH NOTIFICATION] Sending to {user_id}: "
            f"title='{title}', body='{body}', data={json.dumps(data)}"
        )
        
        # 3. Send to Expo Push API
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                EXPO_PUSH_URL,
                json=notification_payload,
                headers={"Content-Type": "application/json"},
            )
            
            # 4. Handle response
            if response.status_code == 200:
                result = response.json()
                
                # Check for errors in Expo's response
                if "data" in result and isinstance(result["data"], list):
                    for item in result["data"]:
                        if item.get("status") == "error":
                            error_code = item.get("details", {}).get("error")
                            
                            # Handle DeviceNotRegistered error
                            if error_code == "DeviceNotRegistered":
                                logger.warning(
                                    f"[PUSH NOTIFICATION] Device not registered for user {user_id}, "
                                    f"deleting invalid token"
                                )
                                await delete_push_token(db, user_id)
                                return False
                            
                            logger.error(
                                f"[PUSH NOTIFICATION] Expo error for user {user_id}: {item}"
                            )
                            return False
                
                logger.info(f"✅ [PUSH NOTIFICATION] Successfully sent to {user_id}")
                return True
            else:
                logger.error(
                    f"[PUSH NOTIFICATION] Expo API returned {response.status_code}: "
                    f"{response.text}"
                )
                return False
                
    except httpx.TimeoutException:
        logger.error(f"[PUSH NOTIFICATION] Timeout sending to user {user_id}")
        return False
    except Exception as e:
        logger.error(f"[PUSH NOTIFICATION] Error sending to user {user_id}: {e}")
        return False

async def delete_push_token(db: AsyncSession, user_id: str) -> None:
    """Delete invalid push token from database."""
    try:
        # Import here to avoid circular dependency
        from sqlalchemy import delete
        from models import UserPushToken
        
        await db.execute(
            delete(UserPushToken).where(UserPushToken.user_id == user_id)
        )
        await db.commit()
        logger.info(f"[PUSH NOTIFICATION] Deleted push token for user {user_id}")
    except Exception as e:
        logger.error(f"[PUSH NOTIFICATION] Error deleting token for user {user_id}: {e}")
        await db.rollback()
