from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, JSON, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from database import Base
import uuid

class UserProfile(Base):
    __tablename__ = 'user_profile'
    
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    wake_time: Mapped[str] = mapped_column(String, nullable=False)  # format "HH:MM"
    bedtime: Mapped[str] = mapped_column(String, nullable=False)    # format "HH:MM"
    timezone: Mapped[str] = mapped_column(String, default='UTC')
    health_anchors: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserPushToken(Base):
    __tablename__ = 'user_push_tokens'
    
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    expo_push_token: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class AgentSession(Base):
    __tablename__ = 'agent_sessions'
    
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)  # format "YYYY-MM-DD"
    state: Mapped[dict] = mapped_column(JSON)  # conversation, tools, context
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ScheduledEvent(Base):
    __tablename__ = 'scheduled_events'
    
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    scheduled_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)  # "checkin", "morning_wake", "evening_reflection"
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # { reason: "...", message: "..." }
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
