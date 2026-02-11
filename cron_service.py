"""
Cron-jobs.org REST API client for dynamic timer creation.

This service manages one-time scheduled jobs via the cron-jobs.org REST API,
enabling precise timer execution without polling.
"""
import os
import logging
import asyncio
from datetime import datetime, timedelta
import httpx

logger = logging.getLogger(__name__)

CRONJOB_API_URL = "https://api.cron-job.org"
CRONJOB_API_KEY = os.getenv("CRONJOB_ORG_API_KEY")
_CRONJOB_API_KEY_MISSING_AT_IMPORT = CRONJOB_API_KEY is None
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8080")
_BACKEND_URL_DEFAULT_AT_IMPORT = (
    BACKEND_URL == "http://localhost:8080" and "BACKEND_URL" not in os.environ
)


def _get_runtime_api_key() -> str | None:
    """Resolve API key at call time to support late-loaded env vars."""
    # If the key existed at import time, treat the module constant as authoritative
    # so tests can reliably patch it.
    if CRONJOB_API_KEY is not None or not _CRONJOB_API_KEY_MISSING_AT_IMPORT:
        return CRONJOB_API_KEY
    return os.getenv("CRONJOB_ORG_API_KEY")


def _get_runtime_backend_url() -> str:
    """Resolve backend URL at call time to support late-loaded env vars."""
    if BACKEND_URL != "http://localhost:8080":
        return BACKEND_URL.rstrip("/")
    if _BACKEND_URL_DEFAULT_AT_IMPORT:
        return os.getenv("BACKEND_URL", BACKEND_URL).rstrip("/")
    return BACKEND_URL.rstrip("/")


async def create_one_time_job(
    target_datetime: datetime,
    event_id: str,
    timezone: str,
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
    api_key = _get_runtime_api_key()
    if not api_key:
        raise ValueError("CRONJOB_ORG_API_KEY environment variable not set")
    if not isinstance(timezone, str) or not timezone.strip():
        raise ValueError("missing_timezone")
    
    # Calculate expiration time (5 minutes after target to ensure one-time execution)
    expires_at = target_datetime + timedelta(minutes=5)
    expires_at_formatted = int(expires_at.strftime("%Y%m%d%H%M%S"))
    
    # Build callback URL
    callback_url = f"{_get_runtime_backend_url()}/api/execute-event/{event_id}"
    
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
                # cron-jobs.org schedule behaves more consistently when wdays
                # is explicitly provided (all weekdays).
                "wdays": [-1],
                "mdays": [target_datetime.day],
                "months": [target_datetime.month],
                "expiresAt": expires_at_formatted
            },
            "requestMethod": 1  # POST
        }
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    max_attempts = int(os.getenv("CRONJOB_API_MAX_RETRIES", "3"))
    for attempt in range(1, max_attempts + 1):
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
            status = e.response.status_code
            response_text = (e.response.text or "").strip()
            request_id = (
                e.response.headers.get("X-Request-Id")
                or e.response.headers.get("x-request-id")
                or "unknown"
            )

            # Retry on server-side and rate-limit failures.
            if status in (429,) or status >= 500:
                if attempt >= max_attempts:
                    logger.error(
                        f"❌ Cron-jobs.org API error after retries: status={status}, request_id={request_id}, body={response_text}"
                    )
                    raise Exception(
                        f"Failed to create cron job (status={status}, request_id={request_id}): {response_text}"
                    )

                backoff_seconds = 2 ** (attempt - 1)
                logger.warning(
                    f"⚠️ Cron-jobs.org transient error (attempt {attempt}/{max_attempts}) "
                    f"status={status}, request_id={request_id}, retrying in {backoff_seconds}s. "
                    f"event_id={event_id}, timezone={timezone}, target={target_datetime.isoformat()}"
                )
                await asyncio.sleep(backoff_seconds)
                continue

            logger.error(
                f"❌ Cron-jobs.org API error: status={status}, request_id={request_id}, body={response_text}"
            )
            raise Exception(
                f"Failed to create cron job (status={status}, request_id={request_id}): {response_text}"
            )
        except Exception as e:
            if attempt < max_attempts:
                backoff_seconds = 2 ** (attempt - 1)
                logger.warning(
                    f"⚠️ Failed to create cron job (attempt {attempt}/{max_attempts}): {e}. "
                    f"Retrying in {backoff_seconds}s."
                )
                await asyncio.sleep(backoff_seconds)
                continue

            logger.error(f"❌ Failed to create cron job: {e}")
            raise

    raise Exception("Failed to create cron job after retries")


async def delete_job(job_id: int) -> bool:
    """
    Deletes a cron job from cron-jobs.org.
    
    Args:
        job_id: The cron-jobs.org job ID to delete
        
    Returns:
        bool: True if deletion succeeded, False otherwise
    """
    api_key = _get_runtime_api_key()
    if not api_key:
        logger.warning("CRONJOB_ORG_API_KEY not set, skipping job deletion")
        return False
    
    if not job_id:
        logger.warning("No job_id provided, skipping deletion")
        return False
    
    headers = {
        "Authorization": f"Bearer {api_key}",
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
