# Agent Tests

Unit tests for the Intentive agent backend.

## Setup

Install test dependencies:

```bash
pip install -r requirements-test.txt
```

## Running Tests

Run all tests:

```bash
pytest
```

Run specific test file:

```bash
pytest tests/test_notification_service.py
```

Run with verbose output:

```bash
pytest -v
```

Run with coverage:

```bash
pytest --cov=. --cov-report=html
```

## Test Structure

### test_notification_service.py

Tests for push notification functionality:

- ✅ Mock Expo API interactions
- ✅ Token retrieval from database
- ✅ Error handling (DeviceNotRegistered, timeouts, API errors)
- ✅ Payload format verification
- ✅ Token deletion on invalid device

## Writing New Tests

Follow these patterns:

1. Use `@pytest.mark.asyncio` for async tests
2. Mock external dependencies (httpx, database)
3. Test success cases and all error paths
4. Verify side effects (database calls, logging)

Example:

```python
@pytest.mark.asyncio
async def test_my_feature(mock_db):
    # Setup
    mock_db.execute.return_value = expected_result
    
    # Execute
    result = await my_function(mock_db)
    
    # Verify
    assert result == expected_value
    mock_db.execute.assert_called_once()
```
