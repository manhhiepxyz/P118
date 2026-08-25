"""Câu sửa lỗi phải tới người dùng NGUYÊN VĂN, không qua Response Agent.

`repair_question` mang dữ kiện mà Response Agent không có cách nào biết: khu
nào kín, ngày nào, khu nào còn trống. Đo nguyên văn trên stack thật, cùng một
workflow, hai câu cùng tồn tại:

    question          "Khu A đã hết chỗ ngày 2026-08-19. Bạn thử Khu B hoặc
                       chọn ngày khác giúp mình nhé."
    assistant_answer  "Bạn ơi, mình cần biết thêm khu vực đỗ xe bạn muốn là
                       Khu A hay Khu B để hoàn tất đăng ký nhé."

Giao diện ưu tiên `answer`, nên người dùng chỉ đọc câu thứ hai — mà họ đã nói
Khu A rồi, nên họ trả lời Khu A lần nữa và hỏng y hệt. Đúng vòng lặp mà
`repair_question` được viết ra để phá, tái xuất hiện ở tầng diễn đạt.
"""

from __future__ import annotations

import pytest

from src.api.routes import _is_repair_question
from src.common.failure_messages import repair_question

# Mọi tổ hợp (tool, code) mà `repair_question` trả câu — giữ ĐỦ.
#
# Test này là lá chắn cho một cơ chế nhận diện bằng chuỗi: thêm một câu sửa lỗi
# mới mà quên dấu hiệu thì câu ấy lại bị model viết lại, và hỏng lặng lẽ y như
# trước. Ở đây nó đỏ.
REPAIR_CASES = [
    ("book_parking", "NO_AVAILABILITY", {"parking_zone": "ZONE_A", "booking_date": "2026-08-19"}),
    ("book_parking", "NO_AVAILABILITY", {"parking_zone": "ZONE_B", "booking_date": "2026-08-19"}),
    ("book_parking", "NO_AVAILABILITY", {}),
    ("book_parking", "BOOKING_ALREADY_EXISTS", {"booking_date": "2026-08-19"}),
    ("book_parking", "BOOKING_ALREADY_EXISTS", {}),
    ("book_shuttle", "NO_AVAILABILITY", {"tour_date": "2026-08-28"}),
    ("book_shuttle", "NO_AVAILABILITY", {}),
    ("schedule_property_viewing", "NO_AVAILABILITY", {"viewing_date": "2026-08-28", "viewing_time": "11:30"}),
    ("schedule_property_viewing", "NO_AVAILABILITY", {}),
    ("register_vehicle", "VEHICLE_ALREADY_EXISTS", {"plate_number": "22A-19238"}),
    ("register_vehicle", "VEHICLE_ALREADY_EXISTS", {}),
]


@pytest.mark.parametrize(("tool", "code", "inputs"), REPAIR_CASES)
def test_every_repair_question_is_recognised(tool: str, code: str, inputs: dict) -> None:
    question = repair_question(tool, code, inputs)
    if question is None:
        pytest.skip(f"{tool}/{code} chưa có câu riêng")
    assert _is_repair_question(question), (
        f"câu sửa lỗi không được nhận ra nên sẽ bị Response Agent viết lại "
        f"và mất lý do: {question!r}"
    )


@pytest.mark.parametrize(
    "question",
    [
        "Mình cần thêm thông tin để lập kế hoạch: khu vực đỗ xe (Khu A hoặc Khu B). Bạn bổ sung giúp mình nhé?",
        "Mình cần thêm thông tin để lập kế hoạch: biển số xe và loại xe. Bạn bổ sung giúp mình nhé?",
        "Để đặt lịch tham quan, mình cần bạn cho biết tên dự án.",
        None,
        "",
    ],
)
def test_ordinary_questions_are_left_to_the_response_agent(question: str | None) -> None:
    """Câu THIẾU THÔNG TIN vẫn nên được diễn đạt tự nhiên.

    Chặn nhầm ở đây thì mọi câu hỏi đều thành văn máy — mất đúng thứ tầng trả
    lời sinh ra để làm.
    """
    assert _is_repair_question(question) is False


@pytest.mark.asyncio
async def test_speak_returns_the_repair_question_untouched(monkeypatch) -> None:
    """Lá chắn cho CHỖ DÙNG, không chỉ cho hàm nhận diện.

    Bản test đầu chỉ kiểm `_is_repair_question`. Xoá thẳng nhánh chặn trong
    `_speak` thì cả 16 test vẫn xanh — một lá chắn không chắn gì. Test này gọi
    đúng `_speak`, và dựng ResponseAgent là lỗi: câu sửa lỗi không được đi qua
    model, kể cả để "viết cho hay hơn".
    """
    from src.api import routes
    from src.models.schemas import DemoWorkflowResponse

    def _explode(*args, **kwargs):
        raise AssertionError("câu sửa lỗi bị đưa qua Response Agent")

    monkeypatch.setattr(routes, "ResponseAgent", _explode)

    question = repair_question(
        "book_parking", "NO_AVAILABILITY", {"parking_zone": "ZONE_A", "booking_date": "2026-08-19"}
    )
    response = DemoWorkflowResponse(
        workflow_id="wf-1",
        status="NEEDS_INFORMATION",
        question=question,
        missing_fields=["parking_zone"],
    )

    spoken = await routes._speak(response, goal="đặt chỗ đỗ xe", capabilities=[])

    assert spoken.answer == question, "câu tới người dùng không còn nguyên văn"
    assert "Khu A" in spoken.answer and "hết chỗ" in spoken.answer
    assert "Khu B" in spoken.answer
