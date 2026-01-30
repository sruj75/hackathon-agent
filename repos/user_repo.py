from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models import UserProfile, UserPushToken
from typing import Optional, List

async def get_profile(db: AsyncSession, user_id: str) -> Optional[UserProfile]:
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    return result.scalar_one_or_none()

async def update_profile(db: AsyncSession, user_id: str, **kwargs) -> UserProfile:
    profile = await get_profile(db, user_id)
    if not profile:
        profile = UserProfile(user_id=user_id, **kwargs)
        db.add(profile)
    else:
        for key, value in kwargs.items():
            setattr(profile, key, value)
    await db.commit()
    await db.refresh(profile)
    return profile

async def get_push_token(db: AsyncSession, user_id: str) -> Optional[str]:
    result = await db.execute(select(UserPushToken).where(UserPushToken.user_id == user_id))
    token_obj = result.scalar_one_or_none()
    return token_obj.expo_push_token if token_obj else None

async def save_push_token(db: AsyncSession, user_id: str, token: str) -> UserPushToken:
    result = await db.execute(select(UserPushToken).where(UserPushToken.user_id == user_id))
    token_obj = result.scalar_one_or_none()
    
    if token_obj:
        token_obj.expo_push_token = token
    else:
        token_obj = UserPushToken(user_id=user_id, expo_push_token=token)
        db.add(token_obj)
        
    await db.commit()
    await db.refresh(token_obj)
    return token_obj

async def get_all_users(db: AsyncSession) -> List[UserProfile]:
    result = await db.execute(select(UserProfile))
    return list(result.scalars().all())
