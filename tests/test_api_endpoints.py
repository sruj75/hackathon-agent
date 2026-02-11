"""API endpoint tests for current backend."""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import main
from auth import AuthUser, get_authenticated_user


@pytest.fixture
async def api_client():
    async def _fake_user():
        return AuthUser(user_id="user_test", email="test@example.com", claims={})

    main.app.dependency_overrides[get_authenticated_user] = _fake_user
    async with AsyncClient(
        transport=ASGITransport(app=main.app),
        base_url="http://test",
    ) as client:
        yield client
    main.app.dependency_overrides.clear()


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
            "token": "ExponentPushToken[abc123]",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "saved", "user_id": "user_test"}
    save_push_token_mock.assert_awaited_once_with("user_test", "ExponentPushToken[abc123]")


@pytest.mark.asyncio
async def test_save_token_rejects_invalid_payload(api_client):
    missing_field = await api_client.post("/api/save-token", json={})
    assert missing_field.status_code == 400
    assert missing_field.json()["detail"] == "Missing token"

    invalid_format = await api_client.post(
        "/api/save-token",
        json={"token": "not-an-expo-token"},
    )
    assert invalid_format.status_code == 400
    assert invalid_format.json()["detail"] == "Invalid token format"


@pytest.mark.asyncio
async def test_execute_event_not_found(api_client, monkeypatch):
    monkeypatch.delenv("EXECUTE_EVENT_SECRET", raising=False)
    monkeypatch.setattr(main.event_repo, "get_by_id", AsyncMock(return_value=None))

    response = await api_client.post("/api/execute-event/event_missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Event not found"


@pytest.mark.asyncio
async def test_execute_event_already_executed(api_client, monkeypatch):
    monkeypatch.delenv("EXECUTE_EVENT_SECRET", raising=False)
    monkeypatch.setattr(
        main.event_repo,
        "get_by_id",
        AsyncMock(return_value={"id": "event_done", "executed": True}),
    )

    response = await api_client.post("/api/execute-event/event_done")

    assert response.status_code == 200
    assert response.json() == {"status": "already_executed"}


@pytest.mark.asyncio
async def test_execute_event_requires_secret_when_configured(api_client, monkeypatch):
    monkeypatch.setenv("EXECUTE_EVENT_SECRET", "shh")
    monkeypatch.setattr(
        main.event_repo,
        "get_by_id",
        AsyncMock(
            return_value={
                "id": "event_123",
                "executed": False,
                "user_id": "user_test",
                "event_type": "checkin",
                "payload": {},
                "cron_job_id": None,
            }
        ),
    )
    monkeypatch.setattr(main.event_repo, "update_event", AsyncMock(return_value=True))
    monkeypatch.setattr(main, "send_push_notification", AsyncMock(return_value=True))
    monkeypatch.setattr(main.cron_service, "delete_job", AsyncMock(return_value=True))

    unauthorized = await api_client.post("/api/execute-event/event_123")
    assert unauthorized.status_code == 401

    authorized = await api_client.post("/api/execute-event/event_123?secret=shh")
    assert authorized.status_code == 200


@pytest.mark.asyncio
async def test_execute_event_processes_and_cleans_up(api_client, monkeypatch):
    monkeypatch.delenv("EXECUTE_EVENT_SECRET", raising=False)
    event = {
        "id": "event_123",
        "user_id": "user_test",
        "event_type": "checkin",
        "payload": {"reason": "deep_work"},
        "executed": False,
        "cron_job_id": 98765,
    }

    get_event_mock = AsyncMock(return_value=event)
    update_event_mock = AsyncMock(return_value=True)
    delete_job_mock = AsyncMock(return_value=True)
    send_push_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(main.event_repo, "get_by_id", get_event_mock)
    monkeypatch.setattr(main.event_repo, "update_event", update_event_mock)
    monkeypatch.setattr(main.cron_service, "delete_job", delete_job_mock)
    monkeypatch.setattr(main, "send_push_notification", send_push_mock)

    response = await api_client.post("/api/execute-event/event_123")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["push_sent"] is True
    update_event_mock.assert_awaited_once()
    update_kwargs = update_event_mock.await_args.kwargs
    assert update_kwargs["executed"] is True
    assert update_kwargs["last_error"] is None
    delete_job_mock.assert_awaited_once_with(98765)
    send_push_mock.assert_awaited_once()
    push_args = send_push_mock.await_args.args
    assert push_args[0] == "user_test"
    assert push_args[1] == "Check-in"
    assert push_args[3]["session_id"].startswith("session_user_test_")
    assert push_args[3]["type"] == "checkin"


@pytest.mark.asyncio
async def test_execute_event_failure_marks_last_error(api_client, monkeypatch):
    monkeypatch.delenv("EXECUTE_EVENT_SECRET", raising=False)
    event = {
        "id": "event_retry",
        "user_id": "user_test",
        "event_type": "calendar_reminder",
        "payload": {
            "event_title": "Standup",
            "event_start_time": "2099-01-01T09:00:00+00:00",
            "timezone": "UTC",
            "calendar_event_id": "gcal_99",
        },
        "executed": False,
        "cron_job_id": 11111,
    }

    get_event_mock = AsyncMock(return_value=event)
    update_event_mock = AsyncMock(return_value=True)
    delete_job_mock = AsyncMock(return_value=True)
    send_push_mock = AsyncMock(return_value=False)

    monkeypatch.setattr(main.event_repo, "get_by_id", get_event_mock)
    monkeypatch.setattr(main.event_repo, "update_event", update_event_mock)
    monkeypatch.setattr(main.cron_service, "delete_job", delete_job_mock)
    monkeypatch.setattr(main, "send_push_notification", send_push_mock)

    response = await api_client.post("/api/execute-event/event_retry")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["push_sent"] is False

    update_event_mock.assert_awaited_once()
    update_args = update_event_mock.await_args
    assert update_args.args[0] == "event_retry"
    assert update_args.kwargs["executed"] is True
    assert update_args.kwargs["last_error"] == "push_failed_or_missing_token"
    delete_job_mock.assert_awaited_once_with(11111)


@pytest.mark.asyncio
async def test_execute_calendar_reminder_missing_timezone_skips_push(api_client, monkeypatch):
    monkeypatch.delenv("EXECUTE_EVENT_SECRET", raising=False)
    event = {
        "id": "event_missing_tz",
        "user_id": "user_test",
        "event_type": "calendar_reminder",
        "payload": {
            "event_title": "Standup",
            "event_start_time": "2099-01-01T09:00:00+00:00",
            "calendar_event_id": "gcal_99",
        },
        "executed": False,
        "cron_job_id": 22222,
    }

    get_event_mock = AsyncMock(return_value=event)
    get_profile_mock = AsyncMock(return_value=None)
    update_event_mock = AsyncMock(return_value=True)
    delete_job_mock = AsyncMock(return_value=True)
    send_push_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(main.event_repo, "get_by_id", get_event_mock)
    monkeypatch.setattr(main.user_repo, "get_profile", get_profile_mock)
    monkeypatch.setattr(main.event_repo, "update_event", update_event_mock)
    monkeypatch.setattr(main.cron_service, "delete_job", delete_job_mock)
    monkeypatch.setattr(main, "send_push_notification", send_push_mock)

    response = await api_client.post("/api/execute-event/event_missing_tz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["push_sent"] is False
    send_push_mock.assert_not_awaited()
    update_event_mock.assert_awaited_once()
    assert update_event_mock.await_args.kwargs["last_error"] == "missing_timezone"
    delete_job_mock.assert_awaited_once_with(22222)


@pytest.mark.asyncio
async def test_get_preferences_not_found(api_client, monkeypatch):
    monkeypatch.setattr(main.user_repo, "get_profile", AsyncMock(return_value=None))

    response = await api_client.get("/api/preferences/me")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_found"
    assert body["is_complete"] is False


@pytest.mark.asyncio
async def test_put_preferences_validates_input(api_client):
    response = await api_client.put(
        "/api/preferences/me",
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
        "/api/preferences/me",
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


@pytest.mark.asyncio
async def test_put_preferences_does_not_overwrite_anchors_when_omitted(
    api_client, monkeypatch
):
    updated_profile = {
        "user_id": "user_test",
        "wake_time": "06:30",
        "bedtime": "22:00",
        "timezone": "UTC",
        "health_anchors": ["sleep", "exercise"],
    }
    update_profile_mock = AsyncMock(return_value=updated_profile)
    resync_mock = AsyncMock(return_value=None)

    monkeypatch.setattr(main.user_repo, "update_profile", update_profile_mock)
    monkeypatch.setattr(main, "_ensure_morning_wake_for_user", resync_mock)

    response = await api_client.put(
        "/api/preferences/me",
        json={
            "wake_time": "06:30",
            "bedtime": "22:00",
            "timezone": "UTC",
        },
    )

    assert response.status_code == 200
    update_profile_mock.assert_awaited_once()
    update_args = update_profile_mock.await_args
    assert update_args.args[0] == "user_test"
    assert "health_anchors" not in update_args.kwargs
