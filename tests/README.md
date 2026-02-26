# Test Suite Guide

This folder is the contract for behavior.
If behavior changes, tests should change in the same pull request.

## Core Principles

1. Test behavior, not implementation details.
2. Default test runs must be deterministic and side-effect free.
3. Live external calls are opt-in only.
4. Every bug fix should come with a regression test.
5. Keep tests simple: clear setup, one reason to fail, direct assertions.

## Test Types In This Repo

- `unit`: Fast tests with isolated dependencies.
- `integration`: Cross-module contract tests (API/repo/service boundaries).
- `regression`: Tests that lock down past bug fixes and critical behavior.
- `live`: Real external-system smoke tests (excluded by default).
- `slow`: Expensive tests that may run in longer pipelines.

## Default Behavior

`pytest.ini` enforces:

- strict marker/config checking
- coverage reporting + `--cov-fail-under=52`
- `-m "not live"` so local/CI default runs do not call external systems

## How To Run Tests

- Full default suite:
  - `pytest`
- Only unit tests:
  - `pytest -m unit --no-cov`
- Only integration tests:
  - `pytest -m integration --no-cov`
- Only regression tests:
  - `pytest -m regression --no-cov`
- Live smoke tests (creates real side effects):
  - `RUN_LIVE_TASK_MANAGEMENT_TESTS=1 pytest -m live --no-cov`

## How To Add A New Test

1. Pick the layer first.
   - Pure function/service logic -> `unit`
   - API or repository contract -> `integration`
   - Bug that must never return -> `regression`
2. Use deterministic inputs.
   - Freeze time (`freezegun`/`time_machine`) instead of `datetime.now()` when time matters.
   - Mock network and external APIs for non-live tests.
3. Assert outcome, not logs/prints.
   - No manual verification steps.
   - No `print`-driven test logic.
4. Add/keep the right marker.
5. Run the smallest relevant subset first, then run full `pytest`.

## How To Update Tests During Feature Development

When behavior changes intentionally:

1. Update existing failing tests to match the new contract.
2. Add at least one new test for the new branch/edge case.
3. Add a regression test if this was a bug fix.
4. Verify no duplicate assertions exist across files.
5. Re-run:
   - `pytest -m unit --no-cov` (fast feedback)
   - `pytest` (full default gate)

## Anti-Patterns (Do Not Add)

- Tests that can pass while operations fail.
- Tests that silently `return` on failure instead of asserting.
- Tests that require manual checking of external systems.
- Duplicated endpoint coverage in multiple files without new behavior.
- Flaky time-window assertions without a frozen clock.

## Suggested Development Loop

1. Reproduce issue with a failing test.
2. Implement fix.
3. Make test pass.
4. Run related marker subset.
5. Run full default suite.
6. Ship code + tests together.
