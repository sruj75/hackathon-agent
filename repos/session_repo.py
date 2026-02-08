"""
Session repository implementation with Firestore.

Collections:
- sessions/{session_id} - Agent conversation sessions
"""
from typing import Optional
from datetime import datetime
from firestore import get_firestore


def _where(query, field: str, op: str, value):
    """
    Apply a Firestore where filter using modern API when available.
    Falls back for compatibility with older SDKs and test doubles.
    """
    try:
        from google.cloud.firestore_v1.base_query import FieldFilter  # type: ignore
    except Exception:
        return query.where(field, op, value)

    try:
        return query.where(filter=FieldFilter(field, op, value))
    except TypeError:
        return query.where(field, op, value)


async def save_session(session_id: str, state: dict, user_id: Optional[str] = None, date: Optional[str] = None) -> dict:
    """Save or update session state."""
    db = get_firestore()
    doc_ref = db.collection("sessions").document(session_id)
    doc = doc_ref.get()
    
    if doc.exists:
        # Update existing session
        session_data = doc.to_dict()
        session_data["state"] = state
        session_data["updated_at"] = datetime.utcnow()
        doc_ref.set(session_data, merge=True)
        return session_data
    else:
        # Create new session
        u_id = user_id or state.get("user_id")
        d_str = date or state.get("date", datetime.now().strftime("%Y-%m-%d"))
        
        if not u_id:
            raise ValueError("user_id required in state or args for new session")
        
        session_data = {
            "session_id": session_id,
            "user_id": u_id,
            "date": d_str,
            "state": state,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        doc_ref.set(session_data)
        return session_data


async def get_session(session_id: str) -> Optional[dict]:
    """Get session by session_id."""
    db = get_firestore()
    doc = db.collection("sessions").document(session_id).get()
    
    if doc.exists:
        return doc.to_dict()
    return None


async def get_today_session(user_id: str, date: str) -> Optional[dict]:
    """Get session for user on specific date."""
    db = get_firestore()
    
    # Query sessions by user_id and date
    sessions_ref = db.collection("sessions")
    query = _where(_where(sessions_ref, "user_id", "==", user_id), "date", "==", date).limit(1)
    docs = query.stream()
    
    for doc in docs:
        return doc.to_dict()
    
    return None


async def upsert_session(session_id: str, user_id: str, date: str, state: dict) -> dict:
    """Create or update session with explicit fields."""
    db = get_firestore()
    doc_ref = db.collection("sessions").document(session_id)
    doc = doc_ref.get()
    
    if doc.exists:
        # Update existing
        session_data = doc.to_dict()
        session_data["state"] = state
        session_data["updated_at"] = datetime.utcnow()
        doc_ref.set(session_data, merge=True)
        return session_data
    else:
        # Create new
        session_data = {
            "session_id": session_id,
            "user_id": user_id,
            "date": date,
            "state": state,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        doc_ref.set(session_data)
        return session_data
