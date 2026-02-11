"""Repository tests for Supabase/Postgres-backed repos with in-memory fakes."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from repos import event_repo, session_repo, user_repo


class FakeAcquire:
    def __init__(self, conn: "FakeConnection"):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self):
        self._db: dict[str, dict[str, dict]] = {
            "users": {},
            "push_tokens": {},
            "sessions": {},
            "events": {},
        }

    def acquire(self):
        return FakeAcquire(FakeConnection(self._db))


class FakeConnection:
    def __init__(self, db: dict[str, dict[str, dict]]):
        self._db = db

    async def fetchrow(self, query: str, *args):
        q = " ".join(query.split())

        if q.startswith("INSERT INTO users "):
            user_id, wake_time, bedtime, tz, anchors, created_at, updated_at = args
            row = self._db["users"].get(user_id, {})
            merged = {
                **row,
                "user_id": user_id,
                "wake_time": wake_time,
                "bedtime": bedtime,
                "timezone": tz,
                "health_anchors": deepcopy(anchors),
                "created_at": row.get("created_at", created_at),
                "updated_at": updated_at,
            }
            self._db["users"][user_id] = merged
            return deepcopy(merged)

        if q == "SELECT * FROM users WHERE user_id = $1":
            row = self._db["users"].get(args[0])
            return deepcopy(row) if row else None

        if q.startswith("INSERT INTO push_tokens "):
            user_id, token, created_at, updated_at = args
            row = self._db["push_tokens"].get(user_id, {})
            merged = {
                **row,
                "user_id": user_id,
                "expo_push_token": token,
                "created_at": row.get("created_at", created_at),
                "updated_at": updated_at,
            }
            self._db["push_tokens"][user_id] = merged
            return deepcopy(merged)

        if q == "SELECT expo_push_token FROM push_tokens WHERE user_id = $1":
            row = self._db["push_tokens"].get(args[0])
            return {"expo_push_token": row["expo_push_token"]} if row else None

        if q.startswith("INSERT INTO sessions "):
            (
                session_id,
                user_id,
                date,
                state,
                created_at,
                updated_at,
            ) = args
            row = self._db["sessions"].get(session_id, {})
            merged = {
                **row,
                "session_id": session_id,
                "user_id": user_id,
                "date": date,
                "state": deepcopy(state),
                "created_at": row.get("created_at", created_at),
                "updated_at": updated_at,
            }
            self._db["sessions"][session_id] = merged
            return deepcopy(merged)

        if q == "SELECT * FROM sessions WHERE session_id = $1":
            row = self._db["sessions"].get(args[0])
            return deepcopy(row) if row else None

        if "FROM sessions WHERE user_id = $1 AND date = $2" in q:
            user_id, date = args
            matches = [
                deepcopy(row)
                for row in self._db["sessions"].values()
                if row.get("user_id") == user_id and row.get("date") == date
            ]
            matches.sort(key=lambda x: x.get("updated_at"), reverse=True)
            return matches[0] if matches else None

        if q.startswith("INSERT INTO events "):
            (
                event_id,
                user_id,
                scheduled_time,
                event_type,
                payload,
                executed,
                cron_job_id,
                created_at,
                updated_at,
            ) = args
            row = {
                "id": event_id,
                "user_id": user_id,
                "scheduled_time": scheduled_time,
                "event_type": event_type,
                "payload": deepcopy(payload),
                "executed": executed,
                "cron_job_id": cron_job_id,
                "created_at": created_at,
                "updated_at": updated_at,
                "last_error": None,
                "last_attempt_at": None,
            }
            self._db["events"][event_id] = row
            return deepcopy(row)

        if "FROM events WHERE user_id = $1 AND event_type = $2 AND scheduled_time = $3" in q:
            user_id, event_type, scheduled_time = args
            for row in self._db["events"].values():
                if (
                    row.get("user_id") == user_id
                    and row.get("event_type") == event_type
                    and row.get("scheduled_time") == scheduled_time
                ):
                    return deepcopy(row)
            return None

        if q == "SELECT * FROM events WHERE id = $1":
            row = self._db["events"].get(args[0])
            return deepcopy(row) if row else None

        if "FROM events WHERE user_id = $1 AND event_type = 'morning_wake'" in q:
            user_id, seed_date = args
            matches = []
            for row in self._db["events"].values():
                payload = row.get("payload") or {}
                if (
                    row.get("user_id") == user_id
                    and row.get("event_type") == "morning_wake"
                    and row.get("executed") is False
                    and payload.get("seed_date") == seed_date
                ):
                    matches.append(deepcopy(row))
            matches.sort(key=lambda x: x.get("scheduled_time"))
            return matches[0] if matches else None

        raise NotImplementedError(f"Unhandled fetchrow query: {q}")

    async def fetch(self, query: str, *args):
        q = " ".join(query.split())

        if q == "SELECT * FROM users ORDER BY created_at ASC":
            rows = [deepcopy(row) for row in self._db["users"].values()]
            rows.sort(key=lambda x: x.get("created_at"))
            return rows

        if "FROM events WHERE executed = FALSE AND cron_job_id IS NULL" in q:
            now_utc, limit = args
            rows = [
                deepcopy(row)
                for row in self._db["events"].values()
                if row.get("executed") is False
                and row.get("cron_job_id") is None
                and row.get("scheduled_time") > now_utc
            ]
            rows.sort(key=lambda x: x.get("scheduled_time"))
            return rows[:limit]

        raise NotImplementedError(f"Unhandled fetch query: {q}")

    async def execute(self, query: str, *args):
        q = " ".join(query.split())

        if q == "DELETE FROM push_tokens WHERE user_id = $1":
            deleted = self._db["push_tokens"].pop(args[0], None)
            return "DELETE 1" if deleted else "DELETE 0"

        if q == "UPDATE events SET cron_job_id = $2, updated_at = $3 WHERE id = $1":
            event_id, cron_job_id, updated_at = args
            row = self._db["events"].get(event_id)
            if not row:
                return "UPDATE 0"
            row["cron_job_id"] = cron_job_id
            row["updated_at"] = updated_at
            return "UPDATE 1"

        if q == "UPDATE events SET executed = TRUE, updated_at = $2 WHERE id = $1":
            event_id, updated_at = args
            row = self._db["events"].get(event_id)
            if not row:
                return "UPDATE 0"
            row["executed"] = True
            row["updated_at"] = updated_at
            return "UPDATE 1"

        if q.startswith("UPDATE users SET "):
            user_id = args[0]
            row = self._db["users"].get(user_id)
            if not row:
                return "UPDATE 0"
            set_part = q.split(" SET ", 1)[1].split(" WHERE user_id = $1", 1)[0]
            assignments = [x.strip() for x in set_part.split(",")]
            for assignment in assignments:
                col, ref = assignment.split(" = ")
                idx = int(ref.replace("$", "")) - 1
                row[col] = deepcopy(args[idx])
            return "UPDATE 1"

        if q.startswith("UPDATE events SET "):
            event_id = args[0]
            row = self._db["events"].get(event_id)
            if not row:
                return "UPDATE 0"
            set_part = q.split(" SET ", 1)[1].split(" WHERE id = $1", 1)[0]
            assignments = [x.strip() for x in set_part.split(",")]
            for assignment in assignments:
                col, ref = assignment.split(" = ")
                idx = int(ref.replace("$", "").replace("::jsonb", "")) - 1
                col = col.replace("::jsonb", "")
                row[col] = deepcopy(args[idx])
            return "UPDATE 1"

        raise NotImplementedError(f"Unhandled execute query: {q}")


@pytest.fixture
def fake_pool(monkeypatch):
    pool = FakePool()

    async def _get_pool():
        return pool

    monkeypatch.setattr(user_repo, "get_pool", _get_pool)
    monkeypatch.setattr(session_repo, "get_pool", _get_pool)
    monkeypatch.setattr(event_repo, "get_pool", _get_pool)
    return pool


@pytest.mark.asyncio
async def test_user_repo_profile_and_push_token_round_trip(fake_pool):
    _ = fake_pool

    created = await user_repo.create_profile(
        user_id="user_a",
        wake_time="07:00",
        bedtime="22:30",
        timezone="UTC",
        health_anchors=["sleep"],
    )
    assert created["user_id"] == "user_a"

    fetched = await user_repo.get_profile("user_a")
    assert fetched is not None
    assert fetched["wake_time"] == "07:00"

    updated = await user_repo.update_profile("user_a", wake_time="06:45")
    assert updated["wake_time"] == "06:45"

    await user_repo.save_push_token("user_a", "ExponentPushToken[token_123]")
    token = await user_repo.get_push_token("user_a")
    assert token == "ExponentPushToken[token_123]"

    await user_repo.delete_push_token("user_a")
    deleted_token = await user_repo.get_push_token("user_a")
    assert deleted_token is None


@pytest.mark.asyncio
async def test_session_repo_upsert_save_and_lookup(fake_pool):
    _ = fake_pool

    await session_repo.upsert_session(
        session_id="session_1",
        user_id="user_a",
        date="2026-02-08",
        state={"step": 1},
    )
    await session_repo.save_session(
        session_id="session_1",
        state={"step": 2, "user_id": "user_a", "date": "2026-02-08"},
    )

    by_id = await session_repo.get_session("session_1")
    assert by_id is not None
    assert by_id["state"]["step"] == 2

    today = await session_repo.get_today_session("user_a", "2026-02-08")
    assert today is not None
    assert today["session_id"] == "session_1"


@pytest.mark.asyncio
async def test_event_repo_lifecycle_and_queries(fake_pool):
    _ = fake_pool

    now = datetime.now(timezone.utc)
    future_time = now + timedelta(hours=1)

    event = await event_repo.create_event(
        user_id="user_a",
        scheduled_time=future_time,
        event_type="morning_wake",
        payload={"seed_date": "2026-02-08", "timezone": "UTC"},
    )

    fetched = await event_repo.get_by_id(event["id"])
    assert fetched is not None
    assert fetched["event_type"] == "morning_wake"

    updated = await event_repo.update_cron_job_id(event["id"], 12345)
    assert updated is True

    pending = await event_repo.find_pending_morning_event("user_a", "2026-02-08")
    assert pending is not None
    assert pending["id"] == event["id"]

    by_type_time = await event_repo.get_event_by_type_and_time(
        user_id="user_a",
        event_type="morning_wake",
        scheduled_time=future_time,
    )
    assert by_type_time is not None

    missing_cron = await event_repo.create_event(
        user_id="user_a",
        scheduled_time=now + timedelta(hours=2),
        event_type="checkin",
        payload={"reason": "reconcile"},
        cron_job_id=None,
    )
    await event_repo.create_event(
        user_id="user_a",
        scheduled_time=now - timedelta(hours=2),
        event_type="checkin",
        payload={"reason": "past"},
        cron_job_id=None,
    )

    reconcilable = await event_repo.list_future_unexecuted_events_missing_cron()
    reconcilable_ids = {row["id"] for row in reconcilable}
    assert missing_cron["id"] in reconcilable_ids

    await event_repo.mark_executed(event["id"])
    executed = await event_repo.get_by_id(event["id"])
    assert executed["executed"] is True
