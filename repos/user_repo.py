"""
User repository implementation with Firestore.

Collections:
- users/{user_id} - User profiles
- push_tokens/{user_id} - Expo push tokens
"""
from typing import Optional, List
from datetime import datetime
from firestore import get_firestore


async def create_profile(user_id: str, wake_time: str, bedtime: str, timezone: str = "UTC", health_anchors: Optional[List[str]] = None) -> dict:
    """Create a new user profile."""
    db = get_firestore()
    
    profile_data = {
        "user_id": user_id,
        "wake_time": wake_time,
        "bedtime": bedtime,
        "timezone": timezone,
        "health_anchors": health_anchors or [],
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    db.collection("users").document(user_id).set(profile_data)
    return profile_data


async def get_profile(user_id: str) -> Optional[dict]:
    """Get user profile by user_id."""
    db = get_firestore()
    doc = db.collection("users").document(user_id).get()
    
    if doc.exists:
        return doc.to_dict()
    return None


async def update_profile(user_id: str, **kwargs) -> dict:
    """Update or create user profile."""
    db = get_firestore()
    doc_ref = db.collection("users").document(user_id)
    doc = doc_ref.get()
    
    if doc.exists:
        # Update existing profile
        update_data = {**kwargs, "updated_at": datetime.utcnow()}
        doc_ref.update(update_data)
        
        # Fetch and return updated profile
        updated_doc = doc_ref.get()
        return updated_doc.to_dict()
    else:
        # Create new profile with provided kwargs
        profile_data = {
            "user_id": user_id,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            **kwargs
        }
        doc_ref.set(profile_data)
        return profile_data


async def get_push_token(user_id: str) -> Optional[str]:
    """Get Expo push token for user."""
    db = get_firestore()
    doc = db.collection("push_tokens").document(user_id).get()
    
    if doc.exists:
        data = doc.to_dict()
        return data.get("expo_push_token")
    return None


async def save_push_token(user_id: str, token: str) -> dict:
    """Save or update user's Expo push token."""
    db = get_firestore()
    
    token_data = {
        "user_id": user_id,
        "expo_push_token": token,
        "updated_at": datetime.utcnow()
    }
    
    doc_ref = db.collection("push_tokens").document(user_id)
    doc = doc_ref.get()
    
    if not doc.exists:
        token_data["created_at"] = datetime.utcnow()
    
    doc_ref.set(token_data, merge=True)
    return token_data


async def get_all_users() -> List[dict]:
    """Get all user profiles."""
    db = get_firestore()
    users_ref = db.collection("users")
    docs = users_ref.stream()
    
    users = []
    for doc in docs:
        users.append(doc.to_dict())
    
    return users


async def delete_push_token(user_id: str) -> None:
    """Delete push token for user."""
    db = get_firestore()
    db.collection("push_tokens").document(user_id).delete()
