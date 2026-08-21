"""LLM usage tracking — contextvar + LangChain callback → llm_usage table.

Vì sao cần file này (Phase D):

`Planner.plan` gọi `with_structured_output(_PlannerResponse).ainvoke(messages)`.
Kết quả trả về là object Pydantic ĐÃ PARSE — `usage_metadata` của AIMessage
gốc bị nuốt mất, không đọc được ở caller. Đường tin cậy duy nhất là
`BaseCallbackHandler`: LangChain gọi `on_llm_end` với response gốc kèm
`usage_metadata` (nếu provider báo).

Cách hoạt động:

  - `usage_context(...)` set một ContextVar định nghĩa bối cảnh hiện tại
    (workflow_id, stage, run_id). Trả Token để `reset_usage_context` trong
    finally.
  - `LlmUsageLogger` là callback đọc ContextVar. Không có context → no-op.
    Có → tích lũy row vào bộ nhớ (`pending`).
  - `await logger.flush()` — gọi trong finally của caller (đang ở async context)
    — mở pool, INSERT `llm_usage`, đóng pool. Best-effort: lỗi DB chỉ log.

KHÔNG dùng `asyncio.run()` trong callback: `on_llm_end` chạy giữa vòng lặp
async của LangChain, `asyncio.run()` sẽ nổ "cannot be called from a running
event loop". Vì vậy callback CHỈ tích lũy; việc ghi DB xảy ra sau, trong
context async của caller.

KHÔNG lưu prompt/response — chỉ số. workflow_id NULL khi chạy eval.
`stage`: 'plan' | 'replan' | 'eval'.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from src.orchestration.runtime_provider import acquire_repository

logger = logging.getLogger(__name__)

# Bối cảnh usage hiện tại. None = không theo dõi (gọi LLM ngoài demo/eval).
_current_usage: ContextVar[dict[str, Any] | None] = ContextVar("p118_current_usage", default=None)


def usage_context(
    *,
    workflow_id: str | None = None,
    stage: str = "plan",
    run_id: str | None = None,
) -> Token:
    """Set bối cảnh theo dõi usage; trả Token để reset trong finally."""
    return _current_usage.set({"workflow_id": workflow_id, "stage": stage, "run_id": run_id})


def reset_usage_context(token: Token) -> None:
    _current_usage.reset(token)


def current_usage_context() -> dict[str, Any] | None:
    """Bối cảnh hiện tại, cho những nơi cần đọc mà không cần ghi usage.

    Trace dùng nó để gắn nhãn stage/workflow vào dòng log — cùng một nguồn với
    `llm_usage`, nên hai bên không bao giờ nói khác nhau về một lượt gọi.
    """
    return _current_usage.get()


class LlmUsageLogger(BaseCallbackHandler):
    """Callback gom usage của mỗi lần LLM kết thúc.

    No-op khi không có `usage_context` (tránh ghi dữ liệu rác cho các cuộc gọi
    LLM bên ngoài demo/eval). `flush()` mới đụng DB — gọi trong finally của
    caller.
    """

    def __init__(self) -> None:
        super().__init__()
        self.pending: list[dict[str, Any]] = []
        self._started: dict[int, float] = {}

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        import time

        self._started[id(self)] = time.monotonic()

    def on_llm_end(self, response, **kwargs: Any) -> None:
        import time

        context = _current_usage.get()
        if context is None:
            return
        latency_ms: int | None = None
        start = self._started.pop(id(self), None)
        if start is not None:
            latency_ms = int((time.monotonic() - start) * 1000)
        usage = _extract_usage(response)
        self.pending.append(
            {
                "workflow_id": context.get("workflow_id"),
                "stage": context.get("stage"),
                "run_id": context.get("run_id"),
                "latency_ms": latency_ms,
                **usage,
            }
        )

    async def flush(self) -> None:
        """Ghi toàn bộ row đang chờ vào `llm_usage`. Best-effort, không raise."""
        rows = self.pending
        self.pending = []
        if not rows:
            return
        try:
            repository = await acquire_repository()
            pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
            try:
                await _insert_rows(pool, rows)
            finally:
                await pool.close()
        except Exception:  # noqa: BLE001 - usage không được làm vỡ caller
            logger.warning("usage flush failed (%d rows)", len(rows), exc_info=True)


def _extract_usage(response: Any) -> dict[str, Any]:
    """Đọc token từ AIMessage (hoặc LLMResult). Không có → toàn 0.

    `on_llm_end` của ChatOpenAI nhận `LLMResult`; mỗi generation chứa AIMessage
    với `usage_metadata`. Cũng nhận AIMessage trực tiếp (test / stream).
    """
    messages: list[Any] = []

    if isinstance(response, dict):
        generations = response.get("generations") or []
    else:
        generations = getattr(response, "generations", None) or []
    for gen_list in generations:
        for gen in gen_list:
            message = getattr(gen, "message", None)
            if message is not None:
                messages.append(message)

    # AIMessage trực tiếp (nếu provider/caller truyền thẳng).
    if not messages and hasattr(response, "usage_metadata"):
        messages = [response]

    prompt_tokens = completion_tokens = total_tokens = 0
    for message in messages:
        metadata = getattr(message, "usage_metadata", None) or {}
        if isinstance(metadata, dict):
            prompt_tokens += int(metadata.get("input_tokens") or metadata.get("prompt_tokens") or 0)
            completion_tokens += int(metadata.get("output_tokens") or metadata.get("completion_tokens") or 0)
            total_tokens += int(metadata.get("total_tokens") or 0)

    return {
        "provider": "unknown",
        "model": "unknown",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens or (prompt_tokens + completion_tokens),
    }


def _calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Mock pricing calculation based on model name."""
    prompt_rate = 0.0000001
    completion_rate = 0.0000002
    if "gpt-4" in model.lower() or "claude-3-opus" in model.lower():
        prompt_rate = 0.000005
        completion_rate = 0.000015
    elif "claude-3-5-sonnet" in model.lower():
        prompt_rate = 0.000003
        completion_rate = 0.000015
    return (prompt_tokens * prompt_rate) + (completion_tokens * completion_rate)


async def _insert_rows(pool: Any, rows: list[dict[str, Any]]) -> None:
    """INSERT nhiều row trong một transaction và cộng dồn metrics vào workflows."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            workflow_updates = {}
            for row in rows:
                workflow_id = row.get("workflow_id")
                await conn.execute(
                    """
                    INSERT INTO llm_usage (
                        workflow_id, run_id, stage, provider, model,
                        prompt_tokens, completion_tokens, total_tokens, latency_ms
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    """,
                    workflow_id,
                    row.get("run_id"),
                    row.get("stage"),
                    row.get("provider"),
                    row.get("model"),
                    row.get("prompt_tokens"),
                    row.get("completion_tokens"),
                    row.get("total_tokens"),
                    row.get("latency_ms"),
                )

                if workflow_id:
                    cost = _calculate_cost(
                        str(row.get("model", "")),
                        int(row.get("prompt_tokens") or 0),
                        int(row.get("completion_tokens") or 0),
                    )
                    if workflow_id not in workflow_updates:
                        workflow_updates[workflow_id] = {"tokens": 0, "cost": 0.0, "latency": 0}
                    workflow_updates[workflow_id]["tokens"] += int(row.get("total_tokens") or 0)
                    workflow_updates[workflow_id]["cost"] += cost
                    workflow_updates[workflow_id]["latency"] += int(row.get("latency_ms") or 0)

            for wf_id, updates in workflow_updates.items():
                await conn.execute(
                    """
                    UPDATE workflows 
                    SET total_tokens = total_tokens + $1,
                        total_cost = total_cost + $2,
                        latency_ms = COALESCE(latency_ms, 0) + $3
                    WHERE workflow_id = $4
                    """,
                    updates["tokens"],
                    updates["cost"],
                    updates["latency"],
                    wf_id,
                )

