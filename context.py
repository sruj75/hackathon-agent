from contextvars import ContextVar
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

# These will hold the state for the current async task
# They are initialized without a default value, so accessing them before setting will raise a LookupError
current_session_id: ContextVar[str] = ContextVar('current_session_id')
current_user_id: ContextVar[str] = ContextVar('current_user_id')
current_db: ContextVar[Optional[AsyncSession]] = ContextVar('current_db', default=None)
