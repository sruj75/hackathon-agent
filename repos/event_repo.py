"""
Event repository implementation with Firestore.

Collections:
- events/{event_id} - Scheduled events (timers, morning wake)
"""
from datetime import datetime, timezone
from typing import Optional, List
import uuid
from firestore import get_firestore


async def create_event(user_id: str, scheduled_time: datetime, event_type: str, payload: dict, cron_job_id: Optional[int] = None) -> dict:
    """Create a new scheduled event."""
    db = get_firestore()
    
    event_id = str(uuid.uuid4())
    event_data = {
        "id": event_id,
        "user_id": user_id,
        "scheduled_time": scheduled_time,
        "event_type": event_type,
        "payload": payload,
        "executed": False,
        "cron_job_id": cron_job_id,
        "created_at": datetime.utcnow()
    }
    
    db.collection("events").document(event_id).set(event_data)
    return event_data


async def update_cron_job_id(event_id: str, cron_job_id: int) -> bool:
    """Update the cron_job_id for an event after creating the cron job."""
    db = get_firestore()
    doc_ref = db.collection("events").document(event_id)
    doc = doc_ref.get()
    
    if not doc.exists:
        return False
    
    doc_ref.update({"cron_job_id": cron_job_id})
    return True


async def mark_executed(event_id: str) -> None:
    """Mark an event as executed."""
    db = get_firestore()
    doc_ref = db.collection("events").document(event_id)
    doc_ref.update({"executed": True})


async def get_event_by_type_and_time(user_id: str, event_type: str, scheduled_time: datetime) -> Optional[dict]:
    """Get event by user_id, event_type, and scheduled_time."""
    db = get_firestore()
    
    # Query events matching criteria
    events_ref = db.collection("events")
    query = (events_ref
             .where("user_id", "==", user_id)
             .where("event_type", "==", event_type)
             .where("scheduled_time", "==", scheduled_time)
             .limit(1))
    
    docs = query.stream()
    for doc in docs:
        return doc.to_dict()
    
    return None


async def get_by_id(event_id: str) -> Optional[dict]:
    """Get event by event_id."""
    db = get_firestore()
    doc = db.collection("events").document(event_id).get()
    
    if doc.exists:
        return doc.to_dict()
    return None


async def find_pending_morning_event(user_id: str, seed_date: str) -> Optional[dict]:
    """
    Find an unexecuted morning_wake event for a user's local date key.

    seed_date is a YYYY-MM-DD string in the user's timezone.
    """
    db = get_firestore()
    query = (
        db.collection("events")
        .where("user_id", "==", user_id)
        .where("event_type", "==", "morning_wake")
        .where("executed", "==", False)
        .where("payload.seed_date", "==", seed_date)
        .limit(1)
    )
    docs = query.stream()
    for doc in docs:
        return doc.to_dict()
    return None


async def list_future_unexecuted_events_missing_cron(limit: int = 200) -> List[dict]:
    """
    Return future unexecuted events that do not yet have cron_job_id assigned.
    Used to reconcile missed cron scheduling after transient failures.
    """
    db = get_firestore()
    now_utc = datetime.now(timezone.utc)
    query = (
        db.collection("events")
        .where("executed", "==", False)
        .where("cron_job_id", "==", None)
        .where("scheduled_time", ">", now_utc)
        .limit(limit)
    )
    return [doc.to_dict() for doc in query.stream()]
