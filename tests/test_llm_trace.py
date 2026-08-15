"""Trace phải im lặng khi tắt, nói đủ khi bật, và không bao giờ làm vỡ request.

Một kênh quan sát chỉ hữu ích nếu người ta tin nó: im lặng phải nghĩa là "model
không chạy", chứ không phải "trace hỏng". Vì vậy ba tính chất được kiểm riêng:
bật/tắt đúng theo môi trường, nội dung có mặt, và mọi lỗi bên trong trace bị
nuốt thay vì ném ngược lên đường chạy workflow.
"""

from __future__ import annotations

import logging

import pytest

from src.monitoring.llm_trace import (
    LlmTraceLogger,
    trace_callbacks,
    trace_enabled,
    trace_full,
    trace_task_result,
)


class _Message:
    def __init__(self, content="", reasoning=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}
        self.response_metadata = {}
        self.type = "ai"


class _Generation:
    def __init__(self, message):
        self.message = message


class _Result:
    def __init__(self, *messages):
        self.generations = [[_Generation(m) for m in messages]]


@pytest.fixture
def trace_lines(caplog):
    caplog.set_level(logging.INFO, logger="p118.llm.trace")
    return caplog


# ---------------------------------------------------------------------------
# Bật / tắt
# ---------------------------------------------------------------------------


def test_trace_is_off_unless_asked_for(monkeypatch):
    """Mặc định tắt: trace in cả nội dung người dùng gõ."""
    monkeypatch.delenv("P118_LLM_TRACE", raising=False)
    monkeypatch.delenv("LLM_TRACE", raising=False)
    assert trace_enabled() is False
    assert trace_callbacks() == []


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_the_documented_ways_to_turn_it_on_all_work(monkeypatch, value):
    monkeypatch.setenv("P118_LLM_TRACE", value)
    assert trace_enabled() is True
    assert len(trace_callbacks()) == 1


@pytest.mark.parametrize("value", ["0", "false", "no", "", "  "])
def test_a_falsey_value_does_not_turn_it_on(monkeypatch, value):
    monkeypatch.delenv("LLM_TRACE", raising=False)
    monkeypatch.setenv("P118_LLM_TRACE", value)
    assert trace_enabled() is False


# ---------------------------------------------------------------------------
# Nội dung
# ---------------------------------------------------------------------------


def test_the_reasoning_chain_is_written_out(trace_lines):
    """Đây là thứ duy nhất `llm_usage` không có, và là lý do file này tồn tại."""
    LlmTraceLogger().on_llm_end(_Result(_Message("Đã đặt lịch.", reasoning="Người dùng muốn xem căn hộ…")))
    text = trace_lines.text
    assert "Người dùng muốn xem căn hộ" in text
    assert "suy luận" in text
    assert "Đã đặt lịch." in text


def test_a_model_without_thinking_still_logs_its_answer(trace_lines):
    """Không có `reasoning_content` là bình thường, không phải lỗi."""
    LlmTraceLogger().on_llm_end(_Result(_Message("Xong.")))
    assert "Xong." in trace_lines.text
    assert "suy luận" not in trace_lines.text


def test_hidden_thinking_is_reported_as_a_token_count(trace_lines):
    """DeepSeek thinking không trả `reasoning_content` qua endpoint OpenAI.

    Nếu log im lặng ở đây thì "model không suy luận" và "provider giấu phần
    suy luận" trông giống hệt nhau — và người demo sẽ kết luận sai.
    """
    message = _Message("42")
    message.response_metadata = {"token_usage": {"completion_tokens_details": {"reasoning_tokens": 58}}}
    LlmTraceLogger().on_llm_end(_Result(message))
    assert "nghĩ 58 token" in trace_lines.text


def test_a_model_that_did_not_think_says_nothing_about_reasoning(trace_lines):
    message = _Message("42")
    message.response_metadata = {"token_usage": {"completion_tokens_details": {"reasoning_tokens": 0}}}
    LlmTraceLogger().on_llm_end(_Result(message))
    assert "suy luận" not in trace_lines.text


def test_the_default_level_does_not_dump_the_prompt(monkeypatch, trace_lines):
    """Prompt hệ thống dài hàng chục dòng và lấp mất phần đáng xem.

    Đây là lý do mức mặc định tồn tại: người demo muốn thấy agent LÀM gì, không
    phải thấy văn bản gửi cho model.
    """
    monkeypatch.setenv("P118_LLM_TRACE", "1")
    LlmTraceLogger().on_chat_model_start({}, [[_Message("Bạn là Planner của hệ thống P-118")]])
    assert "Bạn là Planner" not in trace_lines.text


def test_the_full_level_does_dump_the_prompt(monkeypatch, trace_lines):
    monkeypatch.setenv("P118_LLM_TRACE", "full")
    assert trace_full() is True
    LlmTraceLogger().on_chat_model_start({}, [[_Message("Bạn là Planner của hệ thống P-118")]])
    assert "Bạn là Planner" in trace_lines.text


def test_full_is_not_the_default(monkeypatch):
    monkeypatch.setenv("P118_LLM_TRACE", "1")
    assert trace_enabled() is True
    assert trace_full() is False


# ---------------------------------------------------------------------------
# Tóm tắt: một dòng nói model QUYẾT gì
# ---------------------------------------------------------------------------


def test_a_planner_plan_is_summarised_as_its_tools(trace_lines):
    plan = '{"status": "READY", "plan": {"tasks": [{"tool": "register_vehicle"}, {"tool": "book_parking"}]}}'
    LlmTraceLogger().on_llm_end(_Result(_Message(plan)))
    text = trace_lines.text
    assert "READY" in text and "register_vehicle" in text and "book_parking" in text
    assert "2 tác vụ" in text


def test_a_planner_asking_for_more_says_which_fields(trace_lines):
    LlmTraceLogger().on_llm_end(
        _Result(_Message('{"status": "NEEDS_INFORMATION", "plan": null, "missing_fields": ["max_price"]}'))
    )
    assert "NEEDS_INFORMATION" in trace_lines.text
    assert "max_price" in trace_lines.text


def test_an_answer_is_shown_as_the_sentence_the_user_will_read(trace_lines):
    LlmTraceLogger().on_llm_end(_Result(_Message('{"answer": "Đã đặt lịch xong.", "suggestions": []}')))
    assert "Đã đặt lịch xong." in trace_lines.text
    assert '"suggestions"' not in trace_lines.text


def test_output_that_is_not_json_is_shown_as_is(trace_lines):
    """Thà thô còn hơn giấu: không parse được thì vẫn phải thấy model nói gì."""
    LlmTraceLogger().on_llm_end(_Result(_Message("model nói linh tinh")))
    assert "model nói linh tinh" in trace_lines.text


# ---------------------------------------------------------------------------
# Tác vụ: lý do hỏng phải nằm ngay trên dòng log
# ---------------------------------------------------------------------------


class _TaskResult:
    def __init__(self, error_code=None, message=""):
        self.error_code = error_code
        self.message = message


def test_a_failed_task_logs_the_reason_the_provider_gave(monkeypatch, trace_lines):
    """Chỉ ghi "thất bại" thì người demo phải mở DB mới biết vì sao."""
    monkeypatch.setenv("P118_LLM_TRACE", "1")
    trace_task_result(
        "9b7182a0-0000-0000-0000-000000000000",
        "T2",
        "schedule_property_viewing",
        _TaskResult("PROJECT_NOT_FOUND", "Không có dự án 'Vinhomes Pearl Bay' trong danh mục."),
    )
    text = trace_lines.text
    assert "[chạy/9b7182a0]" in text, "tác vụ bị gắn nhãn giai đoạn lập kế hoạch"
    assert "T2" in text and "schedule_property_viewing" in text
    assert "PROJECT_NOT_FOUND" in text
    assert "Vinhomes Pearl Bay" in text


def test_a_successful_task_logs_one_short_line(monkeypatch, trace_lines):
    monkeypatch.setenv("P118_LLM_TRACE", "1")
    trace_task_result("9b7182a0", "T1", "register_vehicle", _TaskResult())
    assert "T1 register_vehicle → xong" in trace_lines.text


def test_task_logging_is_silent_when_trace_is_off(monkeypatch, trace_lines):
    monkeypatch.delenv("P118_LLM_TRACE", raising=False)
    monkeypatch.delenv("LLM_TRACE", raising=False)
    trace_task_result("9b7182a0", "T1", "register_vehicle", _TaskResult("X", "y"))
    assert trace_lines.text.strip() == ""


@pytest.mark.parametrize("broken", [None, object(), 42])
def test_task_logging_never_raises(monkeypatch, broken):
    monkeypatch.setenv("P118_LLM_TRACE", "1")
    trace_task_result("9b7182a0", "T1", "register_vehicle", broken)


def test_an_empty_completion_is_visible_rather_than_silent(trace_lines):
    """Model trả rỗng mà log cũng rỗng thì người demo không phân biệt được."""
    LlmTraceLogger().on_llm_end(_Result(_Message("")))
    assert "rỗng" in trace_lines.text


def test_the_stage_is_named_so_two_calls_can_be_told_apart(trace_lines):
    from src.monitoring.usage_tracker import reset_usage_context, usage_context

    token = usage_context(workflow_id="1234abcd-0000-0000-0000-000000000000", stage="respond")
    try:
        LlmTraceLogger().on_llm_end(_Result(_Message("Xong.")))
    finally:
        reset_usage_context(token)
    assert "respond/1234abcd" in trace_lines.text


# ---------------------------------------------------------------------------
# Không rò rỉ, không làm vỡ
# ---------------------------------------------------------------------------


def test_anything_shaped_like_a_key_is_masked(trace_lines):
    """Trace là thứ người ta copy vào chat và issue."""
    planted = "sk-abcdef0123456789abcdef"
    LlmTraceLogger().on_llm_end(_Result(_Message(f"Key là {planted} nhé")))
    assert planted not in trace_lines.text
    assert "đã ẩn" in trace_lines.text


def test_a_very_long_reasoning_chain_is_truncated(trace_lines):
    LlmTraceLogger().on_llm_end(_Result(_Message("ok", reasoning="dài " * 5000)))
    assert "cắt" in trace_lines.text


@pytest.mark.parametrize("broken", [None, object(), "không phải LLMResult", 42])
def test_a_malformed_response_never_raises(broken):
    """Trace hỏng thì mất quan sát; trace ném thì mất cả workflow."""
    LlmTraceLogger().on_llm_end(broken)


def test_an_llm_error_is_recorded_without_the_traceback(trace_lines):
    LlmTraceLogger().on_llm_error(RuntimeError("provider từ chối"))
    assert "RuntimeError" in trace_lines.text
    assert "provider từ chối" in trace_lines.text


def test_usage_still_records_no_prompt_text():
    """Hai kênh tách bạch: bảng trong DB vẫn chỉ giữ con số.

    Nếu ai đó về sau đưa nội dung vào `llm_usage`, quyết định ấy phải là cố ý
    và test này phải đỏ trước.
    """
    import pathlib

    ddl = pathlib.Path(__file__).resolve().parent.parent / "src" / "db" / "schema.sql"
    table = ddl.read_text()
    table = table[table.find("CREATE TABLE IF NOT EXISTS llm_usage") :]
    table = table[: table.find(");")]
    for content_column in ("prompt ", "prompt_text", "completion ", "response_text", "reasoning"):
        assert content_column not in table, f"llm_usage đã có cột nội dung: {content_column}"
