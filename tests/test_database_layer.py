"""Repository tests for Firestore-backed repos using an in-memory fake Firestore."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from repos import event_repo, session_repo, user_repo


class FakeDocSnapshot:
    def __init__(self, data: dict | None):
        self._data = deepcopy(data) if data is not None else None

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict:
        return deepcopy(self._data)


class FakeDocRef:
    def __init__(self, collection_store: dict[str, dict], doc_id: str):
        self._collection_store = collection_store
        self._doc_id = doc_id

    def get(self) -> FakeDocSnapshot:
        return FakeDocSnapshot(self._collection_store.get(self._doc_id))

    def set(self, data: dict, merge: bool = False):
        if merge and self._doc_id in self._collection_store:
            merged = deepcopy(self._collection_store[self._doc_id])
            merged.update(deepcopy(data))
            self._collection_store[self._doc_id] = merged
            return
        self._collection_store[self._doc_id] = deepcopy(data)

    def update(self, data: dict):
        if self._doc_id not in self._collection_store:
            raise KeyError(f"Document does not exist: {self._doc_id}")
        self._collection_store[self._doc_id].update(deepcopy(data))

    def delete(self):
        self._collection_store.pop(self._doc_id, None)


class FakeQuery:
    def __init__(self, collection_store: dict[str, dict]):
        self._collection_store = collection_store
        self._filters: list[tuple[str, str, object]] = []
        self._limit: int | None = None

    def where(self, field: str, op: str, value: object):
        self._filters.append((field, op, value))
        return self

    def limit(self, count: int):
        self._limit = count
        return self

    def stream(self):
        docs = []
        for doc in self._collection_store.values():
            if self._matches(doc):
                docs.append(FakeDocSnapshot(doc))
        if self._limit is not None:
            docs = docs[: self._limit]
        return docs

    def _nested_get(self, doc: dict, field: str):
        value = doc
        for part in field.split("."):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value

    def _matches(self, doc: dict) -> bool:
        for field, op, expected in self._filters:
            actual = self._nested_get(doc, field)
            if op == "==" and actual != expected:
                return False
            if op == ">":
                if actual is None or actual <= expected:
                    return False
        return True


class FakeCollection:
    def __init__(self, store: dict[str, dict]):
        self._store = store

    def document(self, doc_id: str) -> FakeDocRef:
        return FakeDocRef(self._store, doc_id)

    def where(self, field: str, op: str, value: object) -> FakeQuery:
        return FakeQuery(self._store).where(field, op, value)

    def limit(self, count: int) -> FakeQuery:
        return FakeQuery(self._store).limit(count)

    def stream(self):
        return [FakeDocSnapshot(doc) for doc in self._store.values()]


class FakeFirestore:
    def __init__(self):
        self._db: dict[str, dict[str, dict]] = {
            "users": {},
            "push_tokens": {},
            "sessions": {},
            "events": {},
        }

    def collection(self, name: str) -> FakeCollection:
        if name not in self._db:
            self._db[name] = {}
        return FakeCollection(self._db[name])


@pytest.fixture
def fake_firestore(monkeypatch):
    db = FakeFirestore()
    monkeypatch.setattr(user_repo, "get_firestore", lambda: db)
    monkeypatch.setattr(session_repo, "get_firestore", lambda: db)
    monkeypatch.setattr(event_repo, "get_firestore", lambda: db)
    return db


@pytest.mark.asyncio
async def test_user_repo_profile_and_push_token_round_trip(fake_firestore):
    _ = fake_firestore

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
async def test_session_repo_upsert_save_and_lookup(fake_firestore):
    _ = fake_firestore

    await session_repo.upsert_session(
        session_id="session_1",
        user_id="user_a",
        date="2026-02-08",
        state={"step": 1},
    )
    await session_repo.save_session(
        session_id="session_1",
        state={"step": 2},
    )

    by_id = await session_repo.get_session("session_1")
    assert by_id is not None
    assert by_id["state"]["step"] == 2

    today = await session_repo.get_today_session("user_a", "2026-02-08")
    assert today is not None
    assert today["session_id"] == "session_1"


@pytest.mark.asyncio
async def test_event_repo_lifecycle_and_queries(fake_firestore):
    _ = fake_firestore

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

    # Future event missing cron for reconciliation query
    missing_cron = await event_repo.create_event(
        user_id="user_a",
        scheduled_time=now + timedelta(hours=2),
        event_type="checkin",
        payload={"reason": "reconcile"},
        cron_job_id=None,
    )
    # Past event should be filtered out from reconciliation query
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
