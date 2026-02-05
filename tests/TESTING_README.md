# Comprehensive Testing Guide

This document provides complete instructions for running the integration and regression test suite for Phases 0-3 of the Intentive project.

## Overview

The test suite validates:
- **Phase 0-1**: Database layer, session management, models
- **Phase 2**: Cron service, event execution, agent runtime
- **Phase 3**: Push notifications, API endpoints
- **Integration**: End-to-end flows (morning wake, check-ins, session continuity)
- **Regression**: Ensures Phase 3 didn't break Phase 1-2 functionality

---

## Quick Start

### Backend Tests

```bash
# Navigate to agent directory
cd agent

# Install test dependencies
pip install -r requirements-test.txt

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=. --cov-report=html
```

### Frontend Tests

```bash
# Navigate to frontend directory
cd frontend

# Run all tests
npm test

# Run specific test file
npm test -- deepLinking.test.ts

# Run with coverage
npm test -- --coverage
```

---

## Backend Test Suite Structure

### Test Files

```
agent/tests/
├── conftest.py                     # Shared fixtures and test configuration
├── test_database_layer.py          # User/session/event repositories
├── test_session_manager.py         # ADK session lifecycle
├── test_cron_service.py            # Dynamic cron job management
├── test_agent_runtime.py           # Agent thinking mode execution
├── test_api_endpoints.py           # FastAPI routes
├── test_notification_service.py    # Push notification service (existing)
├── test_integration_flows.py       # End-to-end scenarios
├── test_phase1_regression.py       # Phase 1 regression suite
└── test_phase2_regression.py       # Phase 2 regression suite
```

### Running Tests by Category

```bash
# Unit tests only (fast)
pytest -m "unit"

# Integration tests only (slower)
pytest -m "integration"

# Regression tests only
pytest -m "regression"

# Skip integration tests (for quick feedback)
pytest -m "not integration"
```

### Running Specific Test Files

```bash
# Database layer tests
pytest tests/test_database_layer.py

# Session manager tests
pytest tests/test_session_manager.py

# Cron service tests
pytest tests/test_cron_service.py

# Agent runtime tests
pytest tests/test_agent_runtime.py

# API endpoint tests
pytest tests/test_api_endpoints.py

# Integration tests
pytest tests/test_integration_flows.py

# Regression tests
pytest tests/test_phase1_regression.py tests/test_phase2_regression.py
```

### Running Specific Tests

```bash
# Run specific test class
pytest tests/test_database_layer.py::TestUserRepository

# Run specific test method
pytest tests/test_database_layer.py::TestUserRepository::test_create_user_profile

# Run tests matching pattern
pytest -k "session"
pytest -k "cron"
pytest -k "notification"
```

---

## Frontend Test Suite Structure

### Test Files

```
frontend/__tests__/
├── README.md                       # Frontend testing overview
├── useNotifications.test.ts        # Push notification hook (existing)
├── deepLinking.test.ts             # Notification tap handling
├── useWebSocketAgent.test.ts       # WebSocket connection
└── integration.test.ts             # Frontend-backend integration
```

### Running Frontend Tests

```bash
# All tests
npm test

# Watch mode (auto-rerun on changes)
npm test -- --watch

# Specific test file
npm test -- deepLinking.test.ts

# With coverage
npm test -- --coverage

# Verbose output
npm test -- --verbose
```

---

## Test Coverage Analysis

### Generate Coverage Reports

```bash
# Backend coverage
cd agent
pytest --cov=. --cov-report=html
open htmlcov/index.html

# Frontend coverage
cd frontend
npm test -- --coverage
open coverage/lcov-report/index.html
```

### Coverage Goals

- **Unit tests**: 80%+ coverage for core modules
- **Integration tests**: Critical paths covered
- **Regression tests**: All Phase 1-2 features validated

---

## Interpreting Test Results

### Successful Test Run

```
============================== test session starts ===============================
platform darwin -- Python 3.11.7, pytest-7.4.3
collected 150 items

tests/test_database_layer.py ....................               [ 13%]
tests/test_session_manager.py ............                     [ 21%]
tests/test_cron_service.py ................                    [ 32%]
tests/test_agent_runtime.py ..........                         [ 39%]
tests/test_api_endpoints.py ..................                 [ 51%]
tests/test_notification_service.py ..........                  [ 58%]
tests/test_integration_flows.py ........                       [ 63%]
tests/test_phase1_regression.py ....................           [ 77%]
tests/test_phase2_regression.py ..................             [ 90%]

============================== 150 passed in 12.34s ===============================
```

### Failed Test Example

```
FAILED tests/test_database_layer.py::TestUserRepository::test_create_user_profile
AssertionError: assert 'user_test' == 'user_wrong'
```

**How to debug:**
1. Read the assertion error
2. Check the test code
3. Verify the implementation
4. Fix the bug
5. Re-run the test

### Skipped Tests

```
tests/test_integration_flows.py::test_full_day_cycle SKIPPED (requires external API)
```

Skipped tests are expected in certain environments (e.g., CI without external dependencies).

---

## Common Test Scenarios

### 1. Database Layer Tests

**What they test:**
- User profile CRUD operations
- Push token save/retrieve/delete
- Agent session persistence
- Scheduled event creation/execution

**Example:**
```bash
pytest tests/test_database_layer.py::TestUserRepository -v
```

### 2. Session Management Tests

**What they test:**
- ADK session creation
- Session state sync to DB
- Session restoration from DB
- Daily session ID generation

**Example:**
```bash
pytest tests/test_session_manager.py -v
```

### 3. Cron Service Tests

**What they test:**
- Dynamic cron job creation via cron-jobs.org API
- Job cleanup after execution
- Error handling (API failures, timeouts)

**Example:**
```bash
pytest tests/test_cron_service.py -v
```

### 4. Agent Runtime Tests

**What they test:**
- Thinking mode execution
- Context variable setting
- Session loading and persistence
- Tool execution

**Example:**
```bash
pytest tests/test_agent_runtime.py -v
```

### 5. API Endpoint Tests

**What they test:**
- POST /api/save-token (token validation, storage)
- POST /api/execute-event/{event_id} (event execution, idempotency)
- GET /health (health check)

**Example:**
```bash
pytest tests/test_api_endpoints.py -v
```

### 6. Integration Tests

**What they test:**
- Morning wake flow (end-to-end)
- Check-in flow (timer → cron → agent → notification)
- Session continuity (across server restarts)
- Full day cycle (wake → check-ins → sleep)

**Example:**
```bash
pytest tests/test_integration_flows.py -v
```

### 7. Regression Tests

**What they test:**
- Phase 1 functionality still works
- Phase 2 functionality still works
- No breaking changes introduced by Phase 3

**Example:**
```bash
pytest tests/test_phase1_regression.py tests/test_phase2_regression.py -v
```

---

## Troubleshooting

### Issue: Import Errors

```
ModuleNotFoundError: No module named 'pytest'
```

**Solution:**
```bash
pip install -r requirements-test.txt
```

### Issue: Fixture Not Found

```
fixture 'test_db' not found
```

**Solution:**
Ensure `conftest.py` is in the correct location and contains the fixture.

### Issue: Tests Hang

```
tests/test_integration_flows.py::test_morning_wake_complete_flow [running for 60s...]
```

**Solution:**
- Check for infinite loops in async code
- Verify all async generators properly yield
- Add timeouts to async operations

### Issue: Database Locked

```
sqlite3.OperationalError: database is locked
```

**Solution:**
- Use in-memory database: `sqlite+aiosqlite:///:memory:`
- Ensure proper connection cleanup in fixtures

### Issue: Mock Not Working

```
AttributeError: 'MagicMock' object has no attribute 'put'
```

**Solution:**
- Verify mock setup in conftest.py
- Check that mock is properly scoped to test
- Use `AsyncMock` for async methods

---

## Best Practices

### Writing New Tests

1. **Use fixtures from conftest.py**
   ```python
   async def test_my_feature(test_db, test_user):
       # test_db and test_user are automatically available
       pass
   ```

2. **Mock external APIs**
   ```python
   with patch('cron_service.httpx.AsyncClient') as mock_client:
       # Your test code
       pass
   ```

3. **Test both success and failure paths**
   ```python
   async def test_success_case(test_db):
       # Happy path
       pass
   
   async def test_error_handling(test_db):
       # Error path
       pass
   ```

4. **Use descriptive test names**
   ```python
   # Good
   async def test_user_profile_creation_with_valid_data():
       pass
   
   # Bad
   async def test_user():
       pass
   ```

5. **Keep tests isolated**
   - Each test should work independently
   - Don't rely on test execution order
   - Clean up in fixtures (autouse cleanup fixtures in conftest.py)

---

## Continuous Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  backend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          cd agent
          pip install -r requirements.txt
          pip install -r requirements-test.txt
      - name: Run tests
        run: |
          cd agent
          pytest --cov=. --cov-report=xml
      - name: Upload coverage
        uses: codecov/codecov-action@v3

  frontend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: '18'
      - name: Install dependencies
        run: |
          cd frontend
          npm install
      - name: Run tests
        run: |
          cd frontend
          npm test -- --coverage
```

---

## Performance Benchmarks

### Expected Test Runtimes

- **Unit tests**: < 5 seconds
- **Integration tests**: < 30 seconds
- **All tests**: < 60 seconds

If tests are slower, investigate:
- Database queries (use in-memory DB)
- External API calls (mock them)
- Async operations (add timeouts)

---

## FAQs

### Q: Do I need external API keys to run tests?

**A:** No. All external APIs (Expo, cron-jobs.org, Gemini) are mocked in tests.

### Q: Can I run tests without a database?

**A:** Yes. Tests use an in-memory SQLite database that's created and destroyed for each test.

### Q: How do I skip slow integration tests?

**A:** Use `pytest -m "not integration"` to skip tests marked with `@pytest.mark.integration`.

### Q: What if a test fails intermittently?

**A:** This often indicates a race condition or improper async handling. Add explicit `await` statements and verify mock setup.

### Q: How do I test WebSocket connections?

**A:** WebSocket tests are mocked in the frontend tests. Real WebSocket testing would require a running backend.

---

## Next Steps

After verifying all tests pass:

1. **Fix any failing tests**
2. **Review coverage reports**
3. **Add tests for edge cases**
4. **Set up CI/CD pipeline**
5. **Document any test-specific environment setup**

---

## Support

For questions or issues with tests:
1. Check this README first
2. Review test file documentation
3. Check conftest.py for fixture definitions
4. Review pytest documentation: https://docs.pytest.org/

---

## Summary

This test suite provides comprehensive coverage of Phases 0-3:

- ✅ **Database layer**: All repos and models tested
- ✅ **Session management**: ADK lifecycle validated
- ✅ **Cron service**: Dynamic job creation tested
- ✅ **Agent runtime**: Thinking mode execution verified
- ✅ **API endpoints**: All routes tested
- ✅ **Integration**: End-to-end flows validated (including morning wake, check-ins, and session continuity)
- ✅ **Regression**: Phase 1-2 functionality confirmed intact after Phase 3 updates
- ✅ **Frontend**: Notifications, deep linking, WebSocket connections, and backend integration tested

---

## Technical Implementation Details

For developers maintaining the test suite:

- **Database**: Tests use an in-memory SQLite database (`sqlite+aiosqlite:///:memory:`). The `conftest.py` fixture handles setup, teardown, and broad patching of `SessionLocal` across modules.
- **External APIs**: Expo Push API, cron-jobs.org, and Gemini API are fully mocked using `pytest-mock` and `unittest.mock`.
- **Time**: `freezegun` is used to freeze time in integration and session manager tests for deterministic results.
- **WebSocket**: Frontend WebSocket tests use a mocked global `WebSocket` implementation to verify connection logic and message handling.

**Run all tests regularly to ensure code quality and catch regressions early!**
