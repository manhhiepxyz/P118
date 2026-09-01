"""Một tiếng "đúng" không được đánh thức Planner.

Owner: Thành Bảo (Decision layer)
File: tests/test_a_bare_yes_does_not_wake_the_planner.py

ĐO ĐƯỢC, không phải suy đoán. Bảng `llm_usage` trên stack demo, 86 lượt Planner
thật của người dùng:

    stage=plan     trung vị 32.98s   p90 78.28s   tổng 3390s
    stage=respond  trung vị  1.55s   p90  1.93s   tổng  425s

Planner chiếm 89% toàn bộ thời gian gọi model. Nó chỉ chạy MỘT lượt cho mỗi
workflow (72/79 workflow đúng một lượt) — nên không có lượt gọi thừa nào để
cắt. Cách duy nhất còn lại là đừng đánh thức nó khi nó không có gì để lập.

Độ trễ tỉ lệ gần như tuyệt đối với số token model sinh ra:

    corr(completion_tokens, latency_ms) = 0.994
    corr(prompt_tokens,     latency_ms) = 0.306

Prompt 12k token gần như miễn phí (lượt nhanh nhất: 12.708 token vào, 28 token
ra, 1,4s). Cái đắt là lượt model NGHĨ — 1.288 đến 20.593 token, ~8ms một token.

Và 34/86 lượt (40%) sinh RA KHÔNG TASK NÀO, vẫn tốn trung vị 15,6s. Trong đó có
lượt này, ghi lại nguyên văn từ phiên test:

    goal = "đúng"   →   Planner chạy 64,0 giây   →   0 task

"đúng" một mình không thể là một yêu cầu dịch vụ. Không có gì để lập kế hoạch,
không có gì để hỏi thêm. Sáu mươi tư giây để đi đến kết luận đó là lãng phí
thuần — người dùng ngồi nhìn màn hình, hết tiền token, và nhận về đúng con số 0.

VÌ SAO CHỈ CHẶN LỜI XÁC NHẬN TRỐNG, không chặn cả "xong chưa" / "giờ tôi phải
làm gì": hai câu sau CẦN trạng thái workflow mới trả lời đúng được. Ném chúng
vào speech lane thì chúng nhận câu đóng hộp "Đã rõ, bạn cứ cho mình biết mục
tiêu tiếp theo" — một câu trả lời SAI, chỉ khác là sai trong 0,1 giây thay vì
7 giây. Trả lời sai nhanh không phải là cải thiện. Chúng thuộc về một lane đọc
trạng thái, chưa có; đây không phải chỗ vá tạm.

`/continue` — đường người dùng bấm duyệt/từ chối — KHÔNG gọi `classify`, nên
việc thêm "đúng" vào speech lane không đụng tới lượt phê duyệt nào.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.small_talk import SmallTalk, SpeechType, classify
from src.main import app

# Nguyên văn hoặc sát nguyên văn những gì người dùng đã gõ.
LOI_XAC_NHAN_TRONG = [
    "đúng",
    "Đúng",
    "đúng rồi",
    "đúng vậy",
    "chính xác",
    "chuẩn",
    "chuẩn rồi",
    "vâng",
    "dạ",
    "dạ vâng",
    "ừ",
    "phải rồi",
    "yes",
]


@pytest.mark.parametrize("message", LOI_XAC_NHAN_TRONG)
def test_a_bare_confirmation_never_reaches_the_planner(message: str) -> None:
    """64 giây cho chữ "đúng". Không lần nào nữa."""
    result = classify(message)
    assert isinstance(result, SmallTalk), f"{message!r} vẫn rơi xuống planner"
    assert result.speech_type is SpeechType.ACKNOWLEDGEMENT


# Đây là hàng rào thật sự của luật trên. Người dùng đã gõ đúng câu này:
# "đúng, tôi muốn đổi ngày tham quan sang ngày 28". Nếu chặn theo tiền tố hay
# theo chuỗi con thì chữ "đúng" ở đầu sẽ nuốt luôn yêu cầu đổi ngày phía sau,
# và người dùng nhận "Đã rõ, bạn cứ cho mình biết mục tiêu tiếp theo" cho một
# câu đã nói rất rõ họ muốn gì. Đổi 64 giây lấy một yêu cầu bị nuốt là lỗ.
@pytest.mark.parametrize(
    "message",
    [
        "đúng, tôi muốn đổi ngày tham quan sang ngày 28",
        "đúng rồi, đặt chỗ đỗ xe khu B cho tôi",
        "vâng, đặt lịch tham quan giúp mình",
        "chuẩn rồi nhé, giờ đặt xe đưa đón hộ mình",
        "yes đặt lịch tham quan",
    ],
)
def test_a_confirmation_carrying_a_request_still_reaches_the_planner(message: str) -> None:
    """Lời xác nhận CÓ kèm yêu cầu vẫn phải xuống planner."""
    assert classify(message) is None, f"{message!r} bị speech lane nuốt mất yêu cầu"


@pytest.mark.anyio
async def test_the_chat_route_answers_a_bare_yes_without_a_model_call() -> None:
    """Tầng route, không phải hàm rời: gõ "đúng" phải trả lời ngay."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/chat", json={"message": "đúng"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"] == ""

    # So với chính câu speech lane trả ra, KHÔNG chỉ "có trả lời gì đó".
    #
    # Bản đầu của test này chỉ khẳng định `analysis == ""` và `response` không
    # rỗng — và nó vẫn XANH khi tôi bỏ hẳn `classify()` khỏi route. Nhánh dự
    # phòng (coi mọi câu là service goal) cũng thoả cả hai điều đó. Một test
    # không phân biệt được hai nhánh thì không canh được nhánh nào.
    du_kien = classify("đúng")
    assert isinstance(du_kien, SmallTalk)
    assert payload["response"] == du_kien.reply
