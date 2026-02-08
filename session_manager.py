from typing import Optional, Dict, Any
import logging
from datetime import datetime
from google.adk.sessions import InMemorySessionService, Session

from repos import session_repo

logger = logging.getLogger(__name__)

class ADKSessionManager:
    """
    Manages ADK sessions with persistence bridging.
    Wraps InMemorySessionService to add SQLite sync capabilities.
    """
    
    def __init__(self, service: Optional[InMemorySessionService] = None):
        """
        Initialize the session manager.
        
        Args:
            service: Optional existing InMemorySessionService instance. 
                     If None, a new one is created.
        """
        self.service = service or InMemorySessionService()

    async def get_or_create_session(
        self, 
        app_name: str, 
        user_id: str, 
        session_id: Optional[str] = None
    ) -> Session:
        """
        Get an existing session (RAM or DB) or create a new one.
        
        1. Check RAM (InMemorySessionService)
        2. Check DB (restore to RAM if found)
        3. Create New
        """
        # 1. Check RAM
        session = await self.service.get_session(
            app_name=app_name, 
            user_id=user_id, 
            session_id=session_id
        )
        if session:
            logger.debug(f"Session {session_id} found in memory.")
            return session

        # 2. Check DB and Restore
        if session_id:
            restored = await self.restore_session_from_db(
                app_name=app_name,
                session_id=session_id,
                user_id=user_id
            )
            if restored:
                logger.info(f"Session {session_id} restored from DB.")
                return restored

        # 3. Create New
        logger.info(f"Creating new session for user {user_id}, session {session_id}")
        return await self.service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=session_id
        )

    async def save_agent_session_to_db(
        self, 
        session_id: str, 
        state: Dict[str, Any],
        user_id: Optional[str] = None
    ) -> bool:
        """
        Syncs in-memory state to Firestore via repo.
        
        Args:
            session_id: Session identifier
            state: Dictionary of session state
            user_id: Optional user_id. Required if creating a new session.
                     If None, attempts to find 'user_id' in state.
        """
        # Ensure user_id is available either from arg or state
        u_id = user_id or state.get("user_id")
        date_str = state.get("date", datetime.now().strftime("%Y-%m-%d"))

        try:
            if u_id:
                 # Use upsert which handles creation safely
                 await session_repo.upsert_session(
                     session_id=session_id, 
                     user_id=u_id, 
                     date=date_str, 
                     state=state
                 )
            else:
                 # Fallback to save_session which relies on state having user_id if new
                 await session_repo.save_session(session_id, state)
                 
            logger.debug(f"Saved session {session_id} to DB.")
            return True
        except Exception as e:
            logger.error(f"Failed to save session {session_id}: {e}")
            return False

    async def restore_session_from_db(
        self, 
        app_name: str,
        session_id: str, 
        user_id: str
    ) -> Optional[Session]:
        """
        Loads session from Firestore and injects into RAM.
        
        Args:
            app_name: ADK App Name
            session_id: Session ID to lookup
            user_id: User ID (required for ADK create_session)
            
        Returns:
            Session object if found and restored, else None.
        """
        try:
            db_session = await session_repo.get_session(session_id)
            if not db_session:
                return None
            
            # Create session in memory with restored state.
            # Firestore repo returns a plain dict, not an object with attributes.
            restored_state = (
                db_session.get("state", {})
                if isinstance(db_session, dict)
                else getattr(db_session, "state", {})
            )
            session = await self.service.create_session(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                state=restored_state
            )
            return session
        except Exception as e:
            logger.error(f"Failed to restore session {session_id}: {e}")
            return None

    @staticmethod
    def get_daily_session_id(user_id: str) -> str:
        """
        Generates the deterministic session ID for a user's daily agent thread.
        Format: session_{user_id}_{YYYY-MM-DD}
        
        This ensures that whether we are accessed via Cron (Start/Check-in) 
        or via WebSocket (Voice), we always hit the SAME session.
        """
        today = datetime.now().date().isoformat()
        return f"session_{user_id}_{today}"
