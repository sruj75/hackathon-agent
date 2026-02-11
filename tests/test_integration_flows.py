"""Integration tests for complete end-to-end flows using repository interfaces."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from freezegun import freeze_time

import cron_service
from agent_runtime import AgentRuntime
from repos import event_repo, session_repo
from session_manager import ADKSessionManager


@pytest.fixture
def in_memory_event_repo(monkeypatch):
    """Patch event_repo with a deterministic in-memory store."""
    events: dict[str, dict] = {}

    async def create_event(
        user_id: str,
        scheduled_time: datetime,
        event_type: str,
        payload: dict,
        cron_job_id: int | None = None,
    ) -> dict:
        event_id = f"event_{len(events) + 1}"
        event = {
            "id": event_id,
            "user_id": user_id,
            "scheduled_time": scheduled_time,
            "event_type": event_type,
            "payload": payload,
            "executed": False,
            "cron_job_id": cron_job_id,
        }
        events[event_id] = event
        return event

    async def update_cron_job_id(event_id: str, cron_job_id: int) -> bool:
        event = events.get(event_id)
        if not event:
            return False
        event["cron_job_id"] = cron_job_id
        return True

    async def mark_executed(event_id: str) -> None:
        events[event_id]["executed"] = True

    async def get_by_id(event_id: str) -> dict | None:
        return events.get(event_id)

    monkeypatch.setattr(event_repo, "create_event", create_event)
    monkeypatch.setattr(event_repo, "update_cron_job_id", update_cron_job_id)
    monkeypatch.setattr(event_repo, "mark_executed", mark_executed)
    monkeypatch.setattr(event_repo, "get_by_id", get_by_id)
    return events


@pytest.fixture
def in_memory_session_repo(monkeypatch):
    """Patch session_repo with an in-memory store used by ADKSessionManager."""
    sessions: dict[str, dict] = {}

    async def upsert_session(session_id: str, user_id: str, date: str, state: dict) -> dict:
        record = {
            "session_id": session_id,
            "user_id": user_id,
            "date": date,
            "state": state,
        }
        sessions[session_id] = record
        return record

    async def save_session(
        session_id: str,
        state: dict,
        user_id: str | None = None,
        date: str | None = None,
    ) -> dict:
        record = {
            "session_id": session_id,
            "user_id": user_id or state.get("user_id"),
            "date": date or state.get("date"),
            "state": state,
        }
        sessions[session_id] = record
        return record

    async def get_session(session_id: str) -> dict | None:
        return sessions.get(session_id)

    monkeypatch.setattr(session_repo, "upsert_session", upsert_session)
    monkeypatch.setattr(session_repo, "save_session", save_session)
    monkeypatch.setattr(session_repo, "get_session", get_session)
    return sessions


async def _fake_thinking_mode(user_id: str, trigger_context: str, session_manager, timezone=None):
    """Minimal thinking-mode stub that still exercises session persistence."""
    session_id = ADKSessionManager.get_daily_session_id(user_id)
    session = await session_manager.get_or_create_session(
        app_name="intentive-coach",
        user_id=user_id,
        session_id=session_id,
    )
    persisted = await session_repo.get_session(session_id)
    existing_conversation = []
    if persisted and isinstance(persisted, dict):
        existing_conversation = (
            persisted.get("state", {}).get("conversation", []) or []
        )
    session.state["conversation"] = [
        *existing_conversation,
        {"trigger": trigger_context},
    ]
    session.state["user_id"] = user_id
    session.state["date"] = datetime.now().date().isoformat()
    await session_manager.save_agent_session_to_db(
        session_id=session_id,
        state=session.state,
        user_id=user_id,
    )
    yield MagicMock()


@pytest.mark.integration
class TestMorningWakeFlow:
    """Integration test for morning wake flow."""

    @pytest.mark.asyncio
    async def test_morning_wake_complete_flow(
        self,
        monkeypatch,
        in_memory_event_repo,
        in_memory_session_repo,
        mock_cron_api,
    ):
        wake_time = datetime(2026, 2, 5, 8, 0, 0)
        user_id = "test_user"

        event = await event_repo.create_event(
            user_id=user_id,
            scheduled_time=wake_time,
            event_type="morning_wake",
            payload={"reason": "daily_kickoff"},
        )

        mock_cron_api.put.return_value.json.return_value = {"jobId": 99999}
        with patch("cron_service.CRONJOB_API_KEY", "test_key"):
            cron_job_id = await cron_service.create_one_time_job(
                target_datetime=wake_time,
                event_id=event["id"],
                timezone="UTC",
            )

        updated = await event_repo.update_cron_job_id(event["id"], cron_job_id)
        assert updated is True

        monkeypatch.setattr(AgentRuntime, "run_thinking_mode", _fake_thinking_mode)
        session_manager = MagicMock()
        session_manager.get_or_create_session = AsyncMock(
            return_value=MagicMock(state={})
        )
        session_manager.save_agent_session_to_db = AsyncMock(return_value=True)

        async for _ in AgentRuntime.run_thinking_mode(
            user_id=user_id,
            trigger_context="Morning wake: 8:00 AM",
            session_manager=session_manager,
        ):
            pass

        before_execution = await event_repo.get_by_id(event["id"])
        assert before_execution["executed"] is False

        await event_repo.mark_executed(event["id"])
        final_event = await event_repo.get_by_id(event["id"])
        assert final_event["executed"] is True
        assert final_event["cron_job_id"] == 99999


@pytest.mark.integration
class TestCheckinFlow:
    """Integration test for check-in flow."""

    @pytest.mark.asyncio
    async def test_checkin_complete_flow(
        self,
        monkeypatch,
        in_memory_event_repo,
        in_memory_session_repo,
        mock_cron_api,
    ):
        scheduled_time = datetime.utcnow() + timedelta(minutes=30)
        user_id = "test_user"

        event = await event_repo.create_event(
            user_id=user_id,
            scheduled_time=scheduled_time,
            event_type="checkin",
            payload={"reason": "deep_work_end"},
        )

        mock_cron_api.put.return_value.json.return_value = {"jobId": 55555}
        with patch("cron_service.CRONJOB_API_KEY", "test_key"):
            cron_job_id = await cron_service.create_one_time_job(
                target_datetime=scheduled_time,
                event_id=event["id"],
                timezone="UTC",
            )

        updated = await event_repo.update_cron_job_id(event["id"], cron_job_id)
        assert updated is True

        monkeypatch.setattr(AgentRuntime, "run_thinking_mode", _fake_thinking_mode)
        session_manager = MagicMock()
        session_manager.get_or_create_session = AsyncMock(
            return_value=MagicMock(state={})
        )
        session_manager.save_agent_session_to_db = AsyncMock(return_value=True)

        async for _ in AgentRuntime.run_thinking_mode(
            user_id=user_id,
            trigger_context="Check-in: deep work ended",
            session_manager=session_manager,
        ):
            pass

        await event_repo.mark_executed(event["id"])

        mock_cron_api.delete.return_value.status_code = 200
        with patch("cron_service.CRONJOB_API_KEY", "test_key"):
            deleted = await cron_service.delete_job(cron_job_id)

        assert deleted is True
        final_event = await event_repo.get_by_id(event["id"])
        assert final_event["executed"] is True


@pytest.mark.integration
class TestSessionContinuity:
    """Integration test for session continuity across restarts."""

    @pytest.mark.asyncio
    async def test_session_survives_server_restart(self, in_memory_session_repo):
        user_id = "test_user"

        with freeze_time("2026-02-04 08:00:00"):
            manager1 = ADKSessionManager()
            session_id = ADKSessionManager.get_daily_session_id(user_id)

            session = await manager1.get_or_create_session(
                app_name="test_app",
                user_id=user_id,
                session_id=session_id,
            )
            session.state.update(
                {
                    "user_id": user_id,
                    "date": "2026-02-04",
                    "conversation": [
                        {"role": "user", "message": "Good morning"},
                        {"role": "agent", "message": "Plan ready"},
                        {"role": "user", "message": "Start coding"},
                    ],
                    "current_mode": "PLANNING",
                    "today_tasks": ["code_review", "meeting", "workout"],
                }
            )
            saved = await manager1.save_agent_session_to_db(
                session_id=session_id,
                state=session.state,
                user_id=user_id,
            )
            assert saved is True

        with freeze_time("2026-02-04 10:00:00"):
            manager2 = ADKSessionManager()
            restored_session = await manager2.get_or_create_session(
                app_name="test_app",
                user_id=user_id,
                session_id=session_id,
            )

        assert restored_session.id == session_id
        assert restored_session.state["current_mode"] == "PLANNING"
        assert len(restored_session.state["conversation"]) == 3
        assert restored_session.state["today_tasks"] == [
            "code_review",
            "meeting",
            "workout",
        ]

    @pytest.mark.asyncio
    async def test_multiple_checkins_same_session(
        self,
        monkeypatch,
        in_memory_session_repo,
    ):
        user_id = "test_user"

        monkeypatch.setattr(AgentRuntime, "run_thinking_mode", _fake_thinking_mode)

        with freeze_time("2026-02-04"):
            manager = ADKSessionManager()
            session_id = ADKSessionManager.get_daily_session_id(user_id)

            with freeze_time("2026-02-04 08:00:00"):
                async for _ in AgentRuntime.run_thinking_mode(
                    user_id=user_id,
                    trigger_context="Morning wake",
                    session_manager=manager,
                ):
                    pass

            with freeze_time("2026-02-04 11:00:00"):
                async for _ in AgentRuntime.run_thinking_mode(
                    user_id=user_id,
                    trigger_context="Check-in: deep work ended",
                    session_manager=manager,
                ):
                    pass

            with freeze_time("2026-02-04 14:00:00"):
                async for _ in AgentRuntime.run_thinking_mode(
                    user_id=user_id,
                    trigger_context="Check-in: lunch ended",
                    session_manager=manager,
                ):
                    pass

        db_session = await session_repo.get_session(session_id)
        assert db_session is not None
        assert db_session["user_id"] == user_id
        assert db_session["date"] == "2026-02-04"
        assert len(db_session["state"]["conversation"]) == 3


@pytest.mark.integration
class TestFullDayCycle:
    """Integration test for complete day cycle."""

    @pytest.mark.asyncio
    async def test_full_day_cycle(
        self,
        monkeypatch,
        in_memory_event_repo,
        in_memory_session_repo,
    ):
        user_id = "test_user"

        monkeypatch.setattr(AgentRuntime, "run_thinking_mode", _fake_thinking_mode)

        with freeze_time("2026-02-04"):
            manager = ADKSessionManager()
            session_id = ADKSessionManager.get_daily_session_id(user_id)

            timeline = [
                ("2026-02-04 08:00:00", "morning_wake", "Morning wake: 8:00 AM"),
                ("2026-02-04 11:00:00", "checkin", "Check-in: deep work ended"),
                ("2026-02-04 14:00:00", "checkin", "Check-in: lunch ended"),
                (
                    "2026-02-04 22:00:00",
                    "evening_reflection",
                    "Evening reflection: 10:00 PM",
                ),
            ]

            created_events = []
            for frozen_at, event_type, trigger_context in timeline:
                with freeze_time(frozen_at):
                    event = await event_repo.create_event(
                        user_id=user_id,
                        scheduled_time=datetime.now(),
                        event_type=event_type,
                        payload={"reason": trigger_context},
                    )
                    created_events.append(event)

                    async for _ in AgentRuntime.run_thinking_mode(
                        user_id=user_id,
                        trigger_context=trigger_context,
                        session_manager=manager,
                    ):
                        pass

                    await event_repo.mark_executed(event["id"])

        for event in created_events:
            event_check = await event_repo.get_by_id(event["id"])
            assert event_check["executed"] is True

        db_session = await session_repo.get_session(session_id)
        assert db_session is not None
        assert db_session["date"] == "2026-02-04"
        assert len(db_session["state"]["conversation"]) == 4
