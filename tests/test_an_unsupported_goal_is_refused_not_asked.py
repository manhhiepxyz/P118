"""Việc không làm được thì NÓI THẲNG, đừng hỏi lại một ô không ai điền được.

Owner: Thành Bảo (Decision layer)
File: tests/test_an_unsupported_goal_is_refused_not_asked.py

Planner trả `missing_fields = ["supported_goal"]` khi mục tiêu nằm ngoài phạm
vi. Nhưng `supported_goal` KHÔNG phải một ô dữ liệu — nó mô tả tình huống. Để
nó ở trạng thái NEEDS_INFORMATION nghĩa là giao diện dựng thẻ "Cần thêm thông
tin" với một ô nhập cho thứ không tồn tại.

CHUỖI ĐÃ ĐO ĐƯỢC trên stack demo:

    Bạn:    Vinhomes Ocean Park       → thiếu: supported_goal
    Bạn:    đổi qua khu B             → thiếu: supported_goal
    Bạn:    đặt chỗ đỗ xe             → thiếu: supported_goal

Người dùng trả lời, hệ thống hỏi lại đúng câu ấy. Bốn lượt như vậy trong dữ
liệu ghi được, và 14 lượt người dùng gõ lại y hệt (689 giây) — họ không có cách
nào thoát.

PHƯƠNG ÁN ĐÃ CHỐT (A): từ chối, giải thích, ĐÓNG LẠI. Không hỏi lại.

Câu từ chối đã có sẵn và đã liệt kê dịch vụ hỗ trợ (`_UNSUPPORTED_GOAL_QUESTION`)
— thứ sai chỉ là TRẠNG THÁI. Nên bản sửa không viết câu mới; nó chuyển sang
nhánh `QUESTION` vốn có: nói một câu, không tạo việc, không dựng thẻ.
"""

from __future__ import annotations

from src.agents.graph import needs_information_update
from src.agents.planner import UNSUPPORTED_GOAL_FIELD


def test_an_unsupported_goal_never_becomes_a_question_card():
    """Không được để trạng thái NEEDS_INFORMATION — đó là thứ dựng ra thẻ."""
    update = needs_information_update((UNSUPPORTED_GOAL_FIELD,), {})
    assert update.get("planner_status") != "NEEDS_INFORMATION", update
    assert not update.get("missing_fields"), (
        "còn missing_fields thì giao diện vẫn dựng ô nhập cho một ô không điền được"
    )


def test_the_refusal_still_says_what_can_be_done():
    """Từ chối mà không chỉ lối đi tiếp thì người dùng vẫn kẹt, chỉ kẹt lịch sự hơn."""
    update = needs_information_update((UNSUPPORTED_GOAL_FIELD,), {})
    noi = " ".join(str(v) for v in update.values())
    assert "tham quan" in noi.lower(), noi


# Ô THẬT vẫn phải hỏi như cũ. Đây là hàng rào: một bản sửa quá tay sẽ biến mọi
# câu hỏi thiếu thông tin thành lời từ chối, và không ai đặt được dịch vụ nữa.
def test_a_real_missing_field_is_still_asked():
    update = needs_information_update(("viewing_date", "viewing_time"), {})
    assert update["planner_status"] == "NEEDS_INFORMATION"
    assert "viewing_date" in update["missing_fields"]
    assert update["question"]


def test_an_unsupported_goal_mixed_with_real_fields_still_refuses():
    """Lẫn lộn thì vẫn là ngoài phạm vi — hỏi ngày cho một việc không làm được
    là bắt người ta điền xong rồi mới nói không."""
    update = needs_information_update((UNSUPPORTED_GOAL_FIELD, "viewing_date"), {})
    assert update.get("planner_status") != "NEEDS_INFORMATION", update
