"""tests/test_llm_usage.py
P-118 — LlmUsageLogger + usage_context (Phase D).

Không cần LLM thật, không cần DB: fake AIMessage kèm usage_metadata, fake
repository. Mục tiêu khoá:

  - logger gom row khi có usage_context; no-op khi không.
  - token đọc đúng từ usage_metadata; total_tokens fallback về sum.
  - flush ghi xuống `llm_usage` (fake pool), best-effort không raise.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.monitoring import usage_tracker
from src.monitoring.usage_tracker import LlmUsageLogger, reset_usage_context, usage_context


def _aimessage_with_usage(*, input_tokens: int, output_tokens: int) -> SimpleNamespace:
    """Fake AIMessage — đúng thứ on_llm_end đọc từ usage_metadata."""
    return SimpleNamespace(
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    )


def test_logger_is_noop_without_usage_context() -> None:
    logger = LlmUsageLogger()
    logger.on_llm_end(_aimessage_with_usage(input_tokens=10, output_tokens=20))

    assert logger.pending == []


def test_logger_accumulates_rows_with_context() -> None:
    token = usage_context(workflow_id="wf-1", stage="plan")
    try:
        logger = LlmUsageLogger()
        logger.on_llm_end(_aimessage_with_usage(input_tokens=10, output_tokens=20))
        logger.on_llm_end(_aimessage_with_usage(input_tokens=30, output_tokens=40))
    finally:
        reset_usage_context(token)

    assert len(logger.pending) == 2
    first = logger.pending[0]
    assert first["workflow_id"] == "wf-1"
    assert first["stage"] == "plan"
    assert first["prompt_tokens"] == 10
    assert first["completion_tokens"] == 20
    assert first["total_tokens"] == 30


def test_usage_context_carries_run_id() -> None:
    token = usage_context(stage="eval", run_id="abc123")
    try:
        logger = LlmUsageLogger()
        logger.on_llm_end(_aimessage_with_usage(input_tokens=5, output_tokens=7))
    finally:
        reset_usage_context(token)

    assert logger.pending[0]["stage"] == "eval"
    assert logger.pending[0]["run_id"] == "abc123"
    assert logger.pending[0]["workflow_id"] is None


def test_context_reset_stops_collection() -> None:
    token = usage_context(stage="plan")
    logger = LlmUsageLogger()
    logger.on_llm_end(_aimessage_with_usage(input_tokens=1, output_tokens=1))
    reset_usage_context(token)

    logger.on_llm_end(_aimessage_with_usage(input_tokens=99, output_tokens=99))

    assert len(logger.pending) == 1  # chỉ row khi có context


@pytest.mark.asyncio
async def test_flush_writes_rows_and_clears_pending(monkeypatch) -> None:
    class _FakePool:
        def __init__(self) -> None:
            self.inserted: list[tuple] = []
            self.closed = False

        def acquire(self):
            return self

        def transaction(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql: str, *params) -> None:
            self.inserted.append(params)

        async def close(self) -> None:
            self.closed = True

    pool = _FakePool()

    class _Repo:
        _pool = pool

    async def _build_repository(*, migrate: bool = True):
        return _Repo()

    monkeypatch.setattr(usage_tracker, "build_repository", _build_repository)

    token = usage_context(workflow_id="wf-9", stage="plan")
    try:
        logger = LlmUsageLogger()
        logger.on_llm_end(_aimessage_with_usage(input_tokens=10, output_tokens=20))
        await logger.flush()
    finally:
        reset_usage_context(token)

    assert len(pool.inserted) == 1
    row = pool.inserted[0]
    assert row[0] == "wf-9"  # workflow_id
    assert row[1] is None  # run_id
    assert row[2] == "plan"  # stage
    assert row[5] == 10  # prompt_tokens
    assert row[6] == 20  # completion_tokens
    assert row[7] == 30  # total_tokens
    assert pool.closed is True
    assert logger.pending == []
