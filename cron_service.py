"""
Cron-jobs.org REST API client for dynamic timer creation.

This service manages one-time scheduled jobs via the cron-jobs.org REST API,
enabling precise timer execution without polling.
"""
import os
import logging
from datetime import datetime, timedelta
import httpx

logger = logging.getLogger(__name__)

CRONJOB_API_URL = "https://api.cron-job.org"
CRONJOB_API_KEY = os.getenv("CRONJOB_ORG_API_KEY")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8080")


async def create_one_time_job(
    target_datetime: datetime,
    event_id: str,
    timezone: str = "UTC"
) -> int:
    """
    Creates a one-time cron job that fires at the specified datetime.
    
    Args:
        target_datetime: When the job should fire (timezone-aware datetime)
        event_id: The ScheduledEvent ID to pass to the callback URL
        timezone: IANA timezone string (e.g., "America/New_York")
        
    Returns:
        int: The cron-jobs.org job ID for later cleanup
        
    Raises:
        Exception: If the API request fails
    """
    if not CRONJOB_API_KEY:
        raise ValueError("CRONJOB_ORG_API_KEY environment variable not set")
    
    # Calculate expiration time (5 minutes after target to ensure one-time execution)
    expires_at = target_datetime + timedelta(minutes=5)
    expires_at_formatted = int(expires_at.strftime("%Y%m%d%H%M%S"))
    
    # Build callback URL
    callback_url = f"{BACKEND_URL}/api/execute-event/{event_id}"
    
    # Prepare job payload
    payload = {
        "job": {
            "url": callback_url,
            "enabled": True,
            "title": f"Timer-{event_id[:8]}",
            "schedule": {
                "timezone": timezone,
                "hours": [target_datetime.hour],
                "minutes": [target_datetime.minute],
                "mdays": [target_datetime.day],
                "months": [target_datetime.month],
                "expiresAt": expires_at_formatted
            },
            "requestMethod": 1  # POST
        }
    }
    
    headers = {
        "Authorization": f"Bearer {CRONJOB_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                f"{CRONJOB_API_URL}/jobs",
                json=payload,
                headers=headers
            )
            response.raise_for_status()
            
            result = response.json()
            job_id = result.get("jobId")
            
            if not job_id:
                raise ValueError(f"No jobId in response: {result}")
            
            logger.info(
                f"✅ Created cron job {job_id} for event {event_id} "
                f"at {target_datetime.isoformat()} ({timezone})"
            )
            
            return job_id
            
    except httpx.HTTPStatusError as e:
        logger.error(
            f"❌ Cron-jobs.org API error: {e.response.status_code} - {e.response.text}"
        )
        raise Exception(f"Failed to create cron job: {e.response.text}")
    except Exception as e:
        logger.error(f"❌ Failed to create cron job: {e}")
        raise


async def delete_job(job_id: int) -> bool:
    """
    Deletes a cron job from cron-jobs.org.
    
    Args:
        job_id: The cron-jobs.org job ID to delete
        
    Returns:
        bool: True if deletion succeeded, False otherwise
    """
    if not CRONJOB_API_KEY:
        logger.warning("CRONJOB_ORG_API_KEY not set, skipping job deletion")
        return False
    
    if not job_id:
        logger.warning("No job_id provided, skipping deletion")
        return False
    
    headers = {
        "Authorization": f"Bearer {CRONJOB_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                f"{CRONJOB_API_URL}/jobs/{job_id}",
                headers=headers
            )
            response.raise_for_status()
            
            logger.info(f"🗑️  Deleted cron job {job_id}")
            return True
            
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.info(f"Cron job {job_id} already deleted or not found")
            return True  # Already gone, consider it success
        logger.error(
            f"❌ Failed to delete cron job {job_id}: {e.response.status_code} - {e.response.text}"
        )
        return False
    except Exception as e:
        logger.error(f"❌ Failed to delete cron job {job_id}: {e}")
        return False
