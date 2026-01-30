from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from models import ScheduledEvent
from datetime import datetime
from typing import Optional, List
import uuid

async def create_event(db: AsyncSession, user_id: str, scheduled_time: datetime, event_type: str, payload: dict) -> ScheduledEvent:
    event = ScheduledEvent(
        id=str(uuid.uuid4()),
        user_id=user_id,
        scheduled_time=scheduled_time,
        event_type=event_type,
        payload=payload,
        executed=False
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event

async def get_pending_events(db: AsyncSession, before_time: datetime) -> List[ScheduledEvent]:
    result = await db.execute(
        select(ScheduledEvent)
        .where(ScheduledEvent.executed == False)
        .where(ScheduledEvent.scheduled_time <= before_time)
    )
    return list(result.scalars().all())

async def mark_executed(db: AsyncSession, event_id: str) -> None:
    result = await db.execute(select(ScheduledEvent).where(ScheduledEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event:
        event.executed = True
        await db.commit()

async def get_event_by_type_and_time(db: AsyncSession, user_id: str, event_type: str, scheduled_time: datetime) -> Optional[ScheduledEvent]:
    # Precision match might be tricky with float times, but datetime exact match is requested in logic.
    result = await db.execute(
        select(ScheduledEvent)
        .where(ScheduledEvent.user_id == user_id)
        .where(ScheduledEvent.event_type == event_type)
        .where(ScheduledEvent.scheduled_time == scheduled_time)
    )
    return result.scalar_one_or_none()

async def get_by_id(db: AsyncSession, event_id: str) -> Optional[ScheduledEvent]:
    result = await db.execute(select(ScheduledEvent).where(ScheduledEvent.id == event_id))
    return result.scalar_one_or_none()
