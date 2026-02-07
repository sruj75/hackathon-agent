"""
Shared pytest fixtures for testing.

NOTE: Tests temporarily disabled during Firestore migration.
"""
import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock

# Set test environment
os.environ['ENVIRONMENT'] = 'test'
os.environ.setdefault('COMPOSIO_CACHE_DIR', '/tmp/composio-cache')
os.makedirs(os.environ['COMPOSIO_CACHE_DIR'], exist_ok=True)


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# TODO: Implement test fixtures with Firestore emulator
