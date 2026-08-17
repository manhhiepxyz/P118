"""Log những gì agent LÀM, đọc được trong lúc đang demo.

`llm_usage` cố ý chỉ ghi CON SỐ (token, độ trễ) — xem docstring của
`usage_tracker`. Đó là quyết định đúng cho một bảng nằm trong DB nghiệp vụ.
Nhưng lúc đứng cạnh máy bấm UI thì cần thấy agent đang làm gì.

Hai mức, vì hai nhu cầu khác nhau:

  P118_LLM_TRACE=1      mỗi việc MỘT dòng — planner quyết gì, tác vụ nào chạy,
                        hỏng ở đâu, câu trả lời ra sao. Đây là mức để demo.
  P118_LLM_TRACE=full   thêm nguyên văn prompt gửi cho model. Chỉ dùng khi
                        nghi ngờ chính prompt sai; nó dài hàng chục dòng mỗi
                        lượt và sẽ lấp mất phần đáng xem.

Mặc định TẮT: trace in cả nội dung người dùng gõ.

Chuỗi suy luận: DeepSeek V4 Flash CÓ chạy thinking nhưng endpoint tương thích
OpenAI không trả `reasoning_content` — chỉ trả `reasoning_tokens`. Vì vậy log
ghi SỐ token đã nghĩ. Con số ấy phân biệt "model không suy luận" với "model có
suy luận nhưng provider không đưa nội dung ra"; thiếu nó, hai trường hợp trông
y hệt nhau. `_reasoning()` vẫn đọc sẵn `reasoning_content` — đổi sang provider
có trả chuỗi thì nó in nguyên văn, không cần sửa gì.

Trace KHÔNG đi ra giao diện: người dùng cuối vẫn chỉ thấy câu đã qua guard của
Response Agent. Và trace không bao giờ là lý do làm hỏng một request — mọi lỗi
bên trong đều bị nuốt. Mất quan sát còn hơn mất workflow.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from src.monitoring.usage_tracker import current_usage_context

# Logger riêng: bật/tắt được độc lập với log ứng dụng.
tracer = logging.getLogger("p118.llm.trace")

# Prompt không chứa key, nhưng trace là thứ người ta copy vào chat/issue —
# rẻ hơn nhiều so với việc phát hiện muộn.
_SECRET = re.compile(r"\b(?:sk|api)[-_][A-Za-z0-9\-_]{8,}", re.IGNORECASE)

_ENV_FLAGS = ("P118_LLM_TRACE", "LLM_TRACE")
_ON = {"1", "true", "yes", "on", "full", "verbose", "2"}
_FULL = {"full", "verbose", "2"}

# Đủ để đọc một câu trả lời trọn vẹn, đủ ngắn để không cuộn mất dòng trước.
MAX_CHARS = 1500


def _flag() -> str:
    for name in _ENV_FLAGS:
        value = os.getenv(name, "").strip().lower()
        if value:
            return value
    return ""


def trace_enabled() -> bool:
    return _flag() in _ON


def trace_full() -> bool:
    """Có in nguyên văn prompt không."""
    return _flag() in _FULL


def _clean(text: Any, limit: int = MAX_CHARS) -> str:
    body = _SECRET.sub("[đã ẩn]", str(text))
    body = " ".join(body.split())
    if len(body) <= limit:
        return body
    return f"{body[:limit]}… [cắt {len(body) - limit} ký tự]"


def _tag(workflow_id: Any = None, stage: str | None = None) -> str:
    context = current_usage_context() or {}
    stage = stage or context.get("stage") or "agent"
    identifier = workflow_id or context.get("workflow_id")
    return f"[{stage}/{str(identifier)[:8]}]" if identifier else f"[{stage}]"


# ---------------------------------------------------------------------------
# Log hành động — gọi từ ngoài, không phụ thuộc LangChain
# ---------------------------------------------------------------------------


def trace_step(message: str, *args: Any, workflow_id: Any = None, stage: str | None = None) -> None:
    """Một dòng mô tả việc agent vừa làm. No-op khi trace tắt.

    `stage` ghi đè nhãn lấy từ `usage_context`: lúc executor chạy tác vụ,
    contextvar vẫn đang là "plan" vì cả lượt nằm trong cùng một context. Để
    nguyên thì log nói tác vụ chạy trong giai đoạn lập kế hoạch — sai.
    """
    if not trace_enabled():
        return
    try:
        tracer.info("%s %s", _tag(workflow_id, stage), message % args if args else message)
    except Exception:  # noqa: BLE001 - trace không được làm vỡ caller
        tracer.debug("trace_step lỗi", exc_info=True)


def trace_task_result(workflow_id: Any, task_id: str, tool: str, result: Any) -> None:
    """Kết quả một tác vụ. Đây là dòng người demo cần nhất khi có lỗi.

    Lý do hỏng phải nằm NGAY trên dòng này: chỉ ghi "thất bại" thì người đọc
    log phải mở DB mới biết provider đã nói gì — và lúc đang demo thì không ai
    mở DB.
    """
    if not trace_enabled():
        return
    try:
        code = getattr(result, "error_code", None)
        code = getattr(code, "value", code)
        if code:
            trace_step(
                "%s %s → HỎNG %s: %s",
                task_id,
                tool,
                code,
                _clean(getattr(result, "message", "") or "(không có mô tả)", 300),
                workflow_id=workflow_id,
                stage="chạy",
            )
        else:
            trace_step("%s %s → xong", task_id, tool, workflow_id=workflow_id, stage="chạy")
    except Exception:  # noqa: BLE001
        tracer.debug("trace_task_result lỗi", exc_info=True)


# ---------------------------------------------------------------------------
# Log LLM — qua callback của LangChain
# ---------------------------------------------------------------------------


def _reasoning(message: Any) -> str:
    for holder in ("additional_kwargs", "response_metadata"):
        bag = getattr(message, holder, None)
        if isinstance(bag, dict):
            for key in ("reasoning_content", "reasoning"):
                value = bag.get(key)
                if isinstance(value, str) and value.strip():
                    return value
    return ""


def _reasoning_tokens(message: Any) -> int:
    metadata = getattr(message, "response_metadata", None)
    if not isinstance(metadata, dict):
        return 0
    details = (metadata.get("token_usage") or {}).get("completion_tokens_details") or {}
    return int(details.get("reasoning_tokens") or 0)


def _messages_of(response: Any) -> list[Any]:
    out: list[Any] = []
    for generations in getattr(response, "generations", None) or []:
        for generation in generations:
            message = getattr(generation, "message", None)
            out.append(message if message is not None else generation)
    return out


def _summarise(content: str) -> str:
    """Một dòng nói model vừa QUYẾT gì, thay vì dán lại cả JSON.

    Planner và Response Agent đều trả JSON. Dán nguyên khối thì mỗi lượt chiếm
    chục dòng và người đọc phải tự tìm chỗ quan trọng. Không parse được thì trả
    nguyên văn — thà thô còn hơn giấu.
    """
    text = content.strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return _clean(text, 400)
    if not isinstance(data, dict):
        return _clean(text, 400)

    # Planner
    if "status" in data and ("plan" in data or "missing_fields" in data):
        status = data.get("status")
        if data.get("missing_fields"):
            return f"{status} — thiếu: {', '.join(str(f) for f in data['missing_fields'])}"
        tasks = ((data.get("plan") or {}).get("tasks")) or []
        tools = ", ".join(str(t.get("tool")) for t in tasks if isinstance(t, dict))
        return f"{status} — {len(tasks)} tác vụ: {tools}" if tools else str(status)

    # Response Agent
    if "answer" in data:
        return f"trả lời: “{_clean(data['answer'], 400)}”"

    return _clean(text, 400)


class LlmTraceLogger(BaseCallbackHandler):
    """Mỗi lượt gọi model thành một hoặc hai dòng đọc được."""

    def __init__(self) -> None:
        super().__init__()
        self._started: dict[Any, float] = {}

    def _remember(self, kwargs: dict[str, Any]) -> None:
        self._started[kwargs.get("run_id")] = time.monotonic()

    def _elapsed(self, kwargs: dict[str, Any]) -> str:
        start = self._started.pop(kwargs.get("run_id"), None)
        return f", {time.monotonic() - start:.1f}s" if start else ""

    # -- prompt: chỉ ở mức `full` -------------------------------------------

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[Any]], **kwargs: Any) -> None:
        self._remember(kwargs)
        if not trace_full():
            return
        try:
            flat = [m for batch in messages for m in batch]
            body = "\n".join(f"  [{getattr(m, 'type', '?')}] {_clean(getattr(m, 'content', m), 2000)}" for m in flat)
            tracer.info("%s ── prompt ──\n%s", _tag(), body)
        except Exception:  # noqa: BLE001
            tracer.debug("trace prompt lỗi", exc_info=True)

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any) -> None:
        self._remember(kwargs)
        if not trace_full():
            return
        try:
            tracer.info("%s ── prompt ──\n%s", _tag(), "\n".join(f"  {_clean(p, 2000)}" for p in prompts))
        except Exception:  # noqa: BLE001
            tracer.debug("trace prompt lỗi", exc_info=True)

    # -- kết quả: luôn in ----------------------------------------------------

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        try:
            elapsed = self._elapsed(kwargs)
            for message in _messages_of(response):
                thought = _reasoning(message)
                tokens = _reasoning_tokens(message)
                note = ""
                if thought:
                    tracer.info("%s suy luận:\n  %s", _tag(), _clean(thought))
                elif tokens:
                    note = f" (nghĩ {tokens} token{elapsed})"
                elif elapsed:
                    note = f" ({elapsed.lstrip(', ')})"

                content = getattr(message, "content", "") or ""
                tool_calls = getattr(message, "tool_calls", None) or []
                if content:
                    tracer.info("%s %s%s", _tag(), _summarise(str(content)), note)
                elif tool_calls:
                    tracer.info("%s gọi tool: %s%s", _tag(), _clean(tool_calls, 300), note)
                else:
                    tracer.info("%s model trả rỗng%s", _tag(), note)
        except Exception:  # noqa: BLE001
            tracer.debug("trace response lỗi", exc_info=True)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        tracer.info("%s model LỖI %s: %s", _tag(), type(error).__name__, _clean(error, 300))


def trace_callbacks() -> list[BaseCallbackHandler]:
    """Callback trace nếu đang bật, ngược lại danh sách rỗng."""
    return [LlmTraceLogger()] if trace_enabled() else []
