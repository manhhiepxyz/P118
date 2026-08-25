"""Model trả nội dung dùng không được → hỏi lại MỘT lần, đừng bỏ cuộc.

Planner có sẵn một vòng corrective retry cho đúng tình huống này. Nhưng bộ phân
loại chỉ nhận `ValidationError`, nên hai loại lỗi parse phổ biến nhất rơi thẳng
vào nhánh "auth/rate-limit/network — hỏi lại cũng vô ích" và request chết hẳn.

Đo được trên stack thật, cùng một câu chạy 18 lần:

    1/18  PLANNING_ERROR
    log   planner thất bại: Planner không gọi được LLM (OutputParserException)

Người dùng đọc "Mình chưa thể tạo kế hoạch từ yêu cầu này. Bạn hãy chọn một dịch
vụ được hỗ trợ hoặc mô tả lại rõ dịch vụ" — một lời khuyên vô nghĩa, vì yêu cầu
của họ hoàn toàn hợp lệ và chính nó chạy trót lọt 17 lần khác.

Lỗi này chỉ đo được sau khi thêm log: hai nhánh bắt lỗi trong `plan_node` trước
đó không ghi gì cả, nên mọi lần lập kế hoạch hỏng đều vô hình.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, ValidationError

from src.agents.planner import Planner, PlannerError, _is_repairable_llm_error
from src.common.task_plan import Task, TaskPlan


class _FailsThenSucceeds:
    """Ném `error` ở lượt đầu, trả kế hoạch hợp lệ ở lượt sau."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.calls = 0

    def with_structured_output(self, _schema, **_kwargs):
        return self

    async def ainvoke(self, _messages):
        self.calls += 1
        if self.calls == 1:
            raise self._error
        from src.agents.planner import _PlannerResponse

        return _PlannerResponse(
            status="READY",
            plan=TaskPlan(
                goal="Đăng ký xe.",
                tasks=[
                    Task(
                        task_id="T1",
                        tool="register_vehicle",
                        depends_on=[],
                        input={
                            "resident_id": "RES-RETRY",
                            "plate_number": "77N-91284",
                            "vehicle_type": "motorcycle",
                        },
                    )
                ],
            ),
            missing_fields=[],
        )


def _a_validation_error() -> ValidationError:
    class _M(BaseModel):
        x: int

    try:
        _M(x="không phải số")
    except ValidationError as exc:
        return exc
    raise AssertionError("không dựng được ValidationError")


def _a_json_error() -> json.JSONDecodeError:
    try:
        json.loads("{ hỏng")
    except json.JSONDecodeError as exc:
        return exc
    raise AssertionError("không dựng được JSONDecodeError")


_PARSE_FAILURES = {
    "OutputParserException": OutputParserException("Không parse được output của model."),
    "JSONDecodeError": _a_json_error(),
    "ValidationError": _a_validation_error(),
}


@pytest.mark.parametrize("ten", sorted(_PARSE_FAILURES))
def test_every_unusable_output_is_worth_asking_again(ten: str):
    assert _is_repairable_llm_error(_PARSE_FAILURES[ten]), (
        f"{ten} nghĩa là model trả nội dung dùng không được — hỏi lại một lần thì sửa được"
    )


@pytest.mark.parametrize("ten", sorted(_PARSE_FAILURES))
@pytest.mark.asyncio
async def test_a_parse_failure_is_retried_and_the_plan_survives(ten: str):
    """Kiểm HÀNH VI, không chỉ bộ phân loại: yêu cầu phải chạy được tới cùng."""
    llm = _FailsThenSucceeds(_PARSE_FAILURES[ten])
    result = await Planner(llm).plan(
        "Đăng ký xe máy biển số 77N-91284.",
        {"resident_id": "RES-RETRY", "resident_verification_status": "VERIFIED"},
    )
    assert llm.calls == 2, "phải hỏi lại đúng một lần"
    assert result.is_ready, f"{ten} làm chết một yêu cầu lẽ ra chạy được"


@pytest.mark.asyncio
async def test_a_hopeless_error_still_fails_fast():
    """Mặc định vẫn là bỏ cuộc ngay. Nới danh sách không được biến thành retry mọi thứ.

    Auth/rate-limit/network hỏi lại cũng vô ích — retry ở đó chỉ nhân đôi thời
    gian chờ và hoá đơn, cho một kết quả không đổi.
    """
    llm = _FailsThenSucceeds(PermissionError("401 Unauthorized"))
    with pytest.raises(PlannerError):
        await Planner(llm).plan("Đăng ký xe máy.", {"resident_id": "RES-RETRY"})
    assert llm.calls == 1, "không được hỏi lại một lỗi xác thực"


def test_the_planner_says_why_it_failed():
    """Hai nhánh bắt lỗi trong `plan_node` phải GHI LẠI lý do.

    Không có dòng log nào thì một lỗi không tất định là không đo được: người
    dùng thấy câu chung chung, còn log trống trơn. Đây là lý do lỗi trên sống
    được lâu.
    """
    import inspect

    from src.agents import graph

    body = inspect.getsource(graph.build_agent_graph) if hasattr(graph, "build_agent_graph") else ""
    if not body:
        body = inspect.getsource(graph)
    dat = body.split("except PlannerError", 1)
    assert len(dat) == 2, "không tìm thấy nhánh bắt PlannerError"
    assert "logger.warning" in dat[1][:600], "planner hỏng mà không ghi lý do — lỗi sẽ vô hình"
