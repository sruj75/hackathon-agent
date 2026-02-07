# Agent Tests

Last validated: **February 7, 2026**  
Status: **115 tests passing**

## Setup

```bash
cd agent
pip install -r requirements-test.txt
```

## Run

```bash
cd agent
pytest
```

## Integration Only

```bash
cd agent
pytest -m integration
```

## Regression Only

```bash
cd agent
pytest -m regression
```

## Suite Map (Current)

- `test_database_layer.py` - repos/models persistence
- `test_session_manager.py` - session lifecycle and restore behavior
- `test_cron_service.py` - cron job create/delete/error handling
- `test_agent_runtime.py` - thinking/conversation mode runtime behavior
- `test_api_endpoints.py` - `/health`, `/api/save-token`, `/api/execute-event/*`
- `test_notification_service.py` - push sending + token invalidation
- `test_phase1_regression.py` - phase 1 safety net
- `test_phase2_regression.py` - phase 2 safety net
- `test_phase6_regression.py` - phase 6 WebSocket init/UI forwarding guards
- `test_integration_flows.py` - end-to-end morning/checkin/day cycle flows

## Scope Rule

Keep each new test focused:
- one primary behavior
- one regression assertion
- external dependencies mocked unless explicitly integration-scoped
