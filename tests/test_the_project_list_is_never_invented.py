"""Hỏi "có những dự án nào" phải nhận danh mục THẬT, không phải khu đỗ xe.

Owner: Thành Bảo (Decision layer)
File: tests/test_the_project_list_is_never_invented.py

Nguyên văn, workflow a39d6ebc trên stack demo:

    Bạn:    có những dự án nào
    P-118:  Hiện tại mình có các dự án: Khu A, Khu B, Khu C.
            Bạn muốn tham quan dự án nào?

"Khu A/B/C" là KHU ĐỖ XE (`ZONE_A`, `ZONE_B`). Không có dự án nào tên như vậy.
Bảy dự án thật là Vinhomes Sài Gòn Park, Global Gate Hạ Long, Hải Vân Bay,
Pearl Bay, Green Paradise, Golden City, Ocean Park.

VÌ SAO NÓ BỊA — và đây mới là điều đáng sợ:

Câu trả lời ĐÚNG đã có sẵn trong code. `_project_catalog_answer()` đọc thẳng
`src/common/projects.PROJECTS` và trả về đủ bảy tên. Nhưng đường duy nhất tới
nó nằm trong `/continue`, sau một cổng `"project_name" in missing_fields` — tức
CHỈ khi đã có một workflow đang chờ người dùng chọn dự án.

Hỏi độc lập thì không đi qua cổng ấy. Lượt gọi đã ghi lại:

    fast_plan  1,31s  → nhường (0 dịch vụ, đây là câu hỏi)
    plan       2,45s  → Planner trả QUESTION, đúng
    respond    1,27s  → Response Agent VIẾT CÂU TRẢ LỜI

Response Agent được giao việc trả lời mà KHÔNG được đưa danh mục. Nó không có
dữ liệu nên lấy thứ gần nhất trong vốn từ của mình — tên các khu đỗ xe.

Không phải model kém. Là ta bảo nó trả lời một câu hỏi rồi không đưa dữ liệu.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.small_talk import SmallTalk, classify
from src.common.projects import PROJECTS
from src.main import app

DU_AN_THAT = [str(p["project_name"]) for p in PROJECTS]


@pytest.mark.parametrize(
    "message",
    [
        "có những dự án nào",
        "Có những dự án nào?",
        "có dự án nào",
        "danh sách dự án",
        "hỗ trợ dự án nào",
        "những dự án nào",
        "dự án nào được hỗ trợ",
    ],
)
def test_asking_for_projects_is_answered_from_the_catalogue(message: str) -> None:
    """Không lượt gọi model nào — danh mục là dữ liệu, không phải thứ để nghĩ ra."""
    result = classify(message)
    assert isinstance(result, SmallTalk), f"{message!r} rơi xuống planner rồi bị bịa"
    for ten in DU_AN_THAT:
        assert ten in result.reply, f"thiếu {ten!r}"


def test_the_answer_never_contains_a_parking_zone() -> None:
    """Chính chuỗi đã hiện ra cho người dùng. Không bao giờ nữa."""
    result = classify("có những dự án nào")
    assert isinstance(result, SmallTalk)
    for khu in ("Khu A", "Khu B", "Khu C", "ZONE_A", "ZONE_B"):
        assert khu not in result.reply


# Hàng rào: đừng nuốt một yêu cầu ĐẶT LỊCH chỉ vì nó có chữ "dự án".
@pytest.mark.parametrize(
    "message",
    [
        "đặt lịch tham quan dự án Vinhomes Pearl Bay ngày 2026-09-10 lúc 09:30",
        "đặt lịch tham quan dự án và chỗ đỗ xe cho tôi",
        "tôi muốn xem căn ở dự án Green Paradise",
    ],
)
def test_a_real_booking_that_mentions_projects_still_reaches_the_planner(message: str) -> None:
    assert classify(message) is None, f"{message!r} bị nuốt mất"


@pytest.mark.anyio
async def test_the_chat_route_lists_real_projects() -> None:
    """Tầng route, không phải hàm rời."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/chat", json={"message": "có những dự án nào"})
    assert response.status_code == 200
    tra_loi = response.json()["response"]
    assert "Vinhomes Pearl Bay" in tra_loi
    assert "Khu A" not in tra_loi


# THỨ TỰ trong `classify` là một luật, không phải sắp xếp tuỳ ý.
#
# Ba câu dưới đây vừa mang ý định dịch vụ (`_has_service_intent` = True) vừa là
# câu hỏi danh mục. `classify` kiểm service-intent TRƯỚC mọi nhánh đóng hộp, nên
# nếu nhánh danh mục đứng sau thì chúng rơi xuống Planner — và đó chính là con
# đường đã sinh ra "Khu A, Khu B, Khu C".
#
# Không có các ca này thì dời nhánh danh mục xuống dưới vẫn xanh hết. Đã thử,
# và đó là lý do chúng có mặt.
@pytest.mark.parametrize(
    "message",
    [
        "đặt lịch tham quan thì có những dự án nào",
        "tôi muốn đặt lịch tham quan, có dự án nào",
        "đăng ký tham quan được những dự án nào",
    ],
)
def test_a_question_wrapped_in_a_service_verb_is_still_answered(message: str) -> None:
    from src.api.small_talk import _has_service_intent

    assert _has_service_intent(message), "ca này chỉ có nghĩa khi service-intent = True"
    result = classify(message)
    assert isinstance(result, SmallTalk), f"{message!r} rơi xuống planner"
    assert "Vinhomes Pearl Bay" in result.reply
    assert "Khu A" not in result.reply
