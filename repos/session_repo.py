from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models import AgentSession
from typing import Optional
from datetime import datetime

async def save_session(db: AsyncSession, session_id: str, state: dict) -> AgentSession:
    # Need user_id and date from state or passed in args?
    # task.md says: `save_session(db, session_id, state)`
    # The model requires user_id and date. 
    # Logic: upsert. If executing update, we might not need user_id but if creating we do.
    # Assuming state contains user_id logic or we fetch existing.
    
    # Let's try to get existing first
    result = await db.execute(select(AgentSession).where(AgentSession.session_id == session_id))
    session = result.scalar_one_or_none()
    
    if session:
        session.state = state
        # user_id and date ideally shouldn't change for a session_id
    else:
        # If new, we need user_id and date. 
        # For now, I'll extract from state if available, or this might need signature change in task.md
        # task.md signatures are often high level. 
        # Let's assume state has user_id and date, or we fail.
        # But wait, 1.5 says: `await session_repo.save_session(db, session_id, agent.state)`
        # agent.state usually has context.
        user_id = state.get('user_id')
        date_str = state.get('date', datetime.now().strftime("%Y-%m-%d"))
        
        if not user_id:
            # Fallback or error? For v0, let's look at get_or_create_session in session_manager logic.
            # Ideally save_session is called on an existing session.
            # But let's handle creation if possible.
            # If user_id is missing, we can't create.
             raise ValueError("user_id required in state for new session")

        session = AgentSession(
            session_id=session_id,
            user_id=user_id,
            date=date_str,
            state=state
        )
        db.add(session)
        
    await db.commit()
    await db.refresh(session)
    return session

async def get_session(db: AsyncSession, session_id: str) -> Optional[AgentSession]:
    result = await db.execute(select(AgentSession).where(AgentSession.session_id == session_id))
    return result.scalar_one_or_none()

async def get_today_session(db: AsyncSession, user_id: str, date: str) -> Optional[AgentSession]:
    result = await db.execute(
        select(AgentSession)
        .where(AgentSession.user_id == user_id)
        .where(AgentSession.date == date)
    )
    return result.scalar_one_or_none()

# Helper for upserting with explicit fields if needed
async def upsert_session(db: AsyncSession, session_id: str, user_id: str, date: str, state: dict) -> AgentSession:
     result = await db.execute(select(AgentSession).where(AgentSession.session_id == session_id))
     session = result.scalar_one_or_none()
     if session:
         session.state = state
         # optionally update user_id/date if meaningful, usually not
     else:
         session = AgentSession(
             session_id=session_id,
             user_id=user_id,
             date=date,
             state=state
         )
         db.add(session)
     await db.commit()
     await db.refresh(session)
     return session
