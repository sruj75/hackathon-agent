"""API endpoint tests for current Firestore-era backend."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

import main


@pytest.fixture
async def api_client():
    async with AsyncClient(
        transport=ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_root_endpoint(api_client):
    response = await api_client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "app" in body
    assert "agent" in body


@pytest.mark.asyncio
async def test_health_endpoint(api_client):
    response = await api_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_save_token_success(api_client, monkeypatch):
    save_push_token_mock = AsyncMock(return_value={"status": "saved"})
    monkeypatch.setattr(main.user_repo, "save_push_token", save_push_token_mock)

    response = await api_client.post(
        "/api/save-token",
        json={
            "user_id": "user_test",
            "token": "ExponentPushToken[abc123]",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "saved", "user_id": "user_test"}
    save_push_token_mock.assert_awaited_once_with("user_test", "ExponentPushToken[abc123]")


@pytest.mark.asyncio
async def test_save_token_rejects_invalid_payload(api_client):
    missing_field = await api_client.post("/api/save-token", json={"user_id": "u1"})
    assert missing_field.status_code == 400
    assert missing_field.json()["detail"] == "Missing user_id or token"

    invalid_format = await api_client.post(
        "/api/save-token",
        json={"user_id": "u1", "token": "not-an-expo-token"},
    )
    assert invalid_format.status_code == 400
    assert invalid_format.json()["detail"] == "Invalid token format"


@pytest.mark.asyncio
async def test_execute_event_not_found(api_client, monkeypatch):
    monkeypatch.setattr(main.event_repo, "get_by_id", AsyncMock(return_value=None))

    response = await api_client.post("/api/execute-event/event_missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Event not found"


@pytest.mark.asyncio
async def test_execute_event_already_executed(api_client, monkeypatch):
    monkeypatch.setattr(
        main.event_repo,
        "get_by_id",
        AsyncMock(return_value={"id": "event_done", "executed": True}),
    )

    response = await api_client.post("/api/execute-event/event_done")

    assert response.status_code == 200
    assert response.json() == {"status": "already_executed"}


@pytest.mark.asyncio
async def test_execute_event_processes_and_cleans_up(api_client, monkeypatch):
    event = {
        "id": "event_123",
        "user_id": "user_test",
        "event_type": "checkin",
        "payload": {"reason": "deep_work", "timezone": "UTC"},
        "executed": False,
        "cron_job_id": 98765,
    }

    get_event_mock = AsyncMock(return_value=event)
    mark_executed_mock = AsyncMock(return_value=None)
    delete_job_mock = AsyncMock(return_value=True)

    async def fake_thinking_mode(**kwargs):
        _ = kwargs
        yield MagicMock()

    monkeypatch.setattr(main.event_repo, "get_by_id", get_event_mock)
    monkeypatch.setattr(main.event_repo, "mark_executed", mark_executed_mock)
    monkeypatch.setattr(main.cron_service, "delete_job", delete_job_mock)
    monkeypatch.setattr(main.AgentRuntime, "run_thinking_mode", fake_thinking_mode)

    response = await api_client.post("/api/execute-event/event_123")

    assert response.status_code == 200
    assert response.json()["status"] == "executed"
    mark_executed_mock.assert_awaited_once_with("event_123")
    delete_job_mock.assert_awaited_once_with(98765)


@pytest.mark.asyncio
async def test_get_preferences_not_found(api_client, monkeypatch):
    monkeypatch.setattr(main.user_repo, "get_profile", AsyncMock(return_value=None))

    response = await api_client.get("/api/preferences/user_unknown")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_found"
    assert body["is_complete"] is False


@pytest.mark.asyncio
async def test_put_preferences_validates_input(api_client):
    response = await api_client.put(
        "/api/preferences/user_test",
        json={
            "wake_time": "bad",
            "bedtime": "bad",
            "timezone": "Not/A_Timezone",
            "health_anchors": [],
        },
    )

    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert any("wake_time" in err for err in errors)


@pytest.mark.asyncio
async def test_put_preferences_updates_profile_and_resyncs_scheduler(api_client, monkeypatch):
    updated_profile = {
        "user_id": "user_test",
        "wake_time": "07:30",
        "bedtime": "22:15",
        "timezone": "America/New_York",
        "health_anchors": ["sleep"],
    }
    update_profile_mock = AsyncMock(return_value=updated_profile)
    resync_mock = AsyncMock(return_value=None)

    monkeypatch.setattr(main.user_repo, "update_profile", update_profile_mock)
    monkeypatch.setattr(main, "_ensure_morning_wake_for_user", resync_mock)

    response = await api_client.put(
        "/api/preferences/user_test",
        json={
            "wake_time": "07:30",
            "bedtime": "22:15",
            "timezone": "America/New_York",
            "health_anchors": ["sleep"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["scheduler"]["resynced"] is True
    update_profile_mock.assert_awaited_once()
    resync_mock.assert_awaited_once_with(updated_profile)
