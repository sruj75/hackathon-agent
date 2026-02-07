# Agent Testing Quick Guide

Use `/Users/srujanu/Desktop/intentive/agent/tests/README.md` as the source of truth.

## Daily Command

```bash
cd agent
pytest
```

## Current Baseline (February 7, 2026)

- 115 tests
- all passing

## Focused Runs

```bash
cd agent
pytest -m integration
pytest -m regression
```
