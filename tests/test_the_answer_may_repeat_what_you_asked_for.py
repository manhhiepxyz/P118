"""Câu trả lời được phép nhắc lại ngày giờ CHÍNH người dùng vừa nói.

Guard số của Response Agent tồn tại để model không bịa số. Nhưng nó loại luôn
những con số người dùng vừa gõ — trong khi `goal` NẰM TRONG prompt, tức model
được cho đọc rồi bị phạt vì dùng.

Đo được trên stack thật: 14/14 lượt bị loại. Với một yêu cầu hai dịch vụ đang
chờ duyệt, tập số hợp lệ chỉ có đúng một phần tử — số bước:

    số hợp lệ: ['2']

Model viết "ngày 27/08 lúc 10:00" (đúng câu khách vừa nói) và bị loại. Hậu quả
kép: khách luôn nhận câu nền viết sẵn, và mỗi lượt tốn thêm 2 lời gọi model
(~3s) để rồi vứt cả hai.

Điều guard PHẢI giữ là chuyện khác và hẹp hơn: người dùng có thể gõ "phí
100.000" trong khi hoá đơn thật là 150.000. Số TIỀN từ câu người dùng không
được thành sự thật của hệ thống — và đó đã có guard riêng lo (`_MONEY` +
`_reject_untrusted_payment_values` ở tầng Planner).

Ngày, giờ, số khách, biển số thì không có rủi ro ấy: chúng là điều người dùng
YÊU CẦU, và nhắc lại đúng lời họ là việc một trợ lý phải làm.
"""

from __future__ import annotations

import pytest

from src.agents.response_agent import (
    AgentReply,
    ReplyView,
    _number_keys,
    _numbers_in_view,
    _reject_reason,
)

GOAL = (
    "Đặt lịch tham quan Vinhomes Green Paradise ngày 2026-08-27 lúc 10:00. "
    "Đăng ký nhận tư vấn Vinhomes Pearl Bay nhu cầu Thuê gọi lúc 10:00"
)


def _cho_duyet(goal: str = GOAL) -> ReplyView:
    """Đúng trạng thái người dùng gặp: hai dịch vụ đang chờ đơn vị duyệt."""
    return ReplyView(
        goal=goal,
        status="WAITING_APPROVAL",
        baseline_message="Đang chờ đơn vị cung cấp dịch vụ xác nhận.",
        steps=[
            {"title": "Đặt lịch tham quan", "status": "WAITING_APPROVAL", "message": "Đang chờ đơn vị xác nhận."},
            {"title": "Đăng ký nhận tư vấn", "status": "WAITING_APPROVAL", "message": "Đang chờ đơn vị xác nhận."},
        ],
    )


def test_the_time_the_customer_asked_for_is_quotable():
    """Đây là lỗi được báo: nhắc lại giờ khách vừa nói mà bị loại."""
    reply = AgentReply(answer="Lịch tham quan ngày 27/08 lúc 10:00 đã được gửi đi.", suggestions=[])

    assert _reject_reason(reply, _cho_duyet()) is None


def test_the_iso_date_from_the_goal_is_quotable():
    reply = AgentReply(answer="Mình đã ghi nhận lịch ngày 2026-08-27.", suggestions=[])

    assert _reject_reason(reply, _cho_duyet()) is None


def test_a_number_nobody_ever_said_is_still_refused():
    """Nới cửa cho goal KHÔNG được biến guard thành vô dụng."""
    reply = AgentReply(answer="Lịch tham quan ngày 99/99 lúc 03:17 đã được gửi.", suggestions=[])

    assert _reject_reason(reply, _cho_duyet()) == "nêu một con số không có trong dữ liệu"


def test_money_from_the_goal_is_refused_even_when_worded_as_unpaid():
    """Ca chỉ `_MONEY.sub` bắt được — và là lý do nó tồn tại.

    Guard tiền (`_MONEY` + `_UNPAID_MARKERS`) chỉ hỏi "có nói rõ là chưa trả
    không". Một câu viết "phí 100.000 VND, bạn xác nhận thanh toán nhé" ĐI QUA
    nó hợp lệ. Nhưng 100.000 là con số KHÁCH tự gõ, không phải báo giá của
    provider — nếu goal được nhận nguyên vẹn thì nó thành một khoản phí có vẻ
    chính thức.

    Không có test này thì việc cắt tiền khỏi goal đi lọt hoàn toàn: mọi test
    tiền khác đều bị guard tiền chặn trước, nên chúng xanh cả khi `_MONEY.sub`
    bị gỡ.
    """
    view = _cho_duyet("Đặt chỗ đỗ xe khu A, phí 100.000 đồng")
    reply = AgentReply(answer="Chỗ đỗ xe khu A, phí 100.000 VND, bạn xác nhận thanh toán nhé.", suggestions=[])

    assert _reject_reason(reply, view) == "nêu một con số không có trong dữ liệu"


def test_money_typed_by_the_customer_never_becomes_a_fact():
    """Ranh giới thật sự cần giữ: số TIỀN người dùng gõ không phải sự thật.

    Khách viết "phí 100.000" trong khi hoá đơn thật là 150.000. Nhắc lại con số
    ấy như một khoản đã chốt là điều guard sinh ra để chặn — và nới cửa cho
    ngày giờ không được nới luôn cho tiền.
    """
    view = _cho_duyet("Đặt chỗ đỗ xe khu A, phí 100.000 đồng")
    reply = AgentReply(answer="Đã đặt chỗ đỗ xe khu A, phí 100.000 VND.", suggestions=[])

    assert _reject_reason(reply, view) is not None


def test_the_goal_widens_the_allowed_set_but_not_without_limit():
    """Tập số hợp lệ lưu dạng ĐÃ CHUẨN HOÁ (`10:00` → `1000`), nên so bằng
    chính hàm chuẩn hoá mà guard dùng — không so chuỗi thô."""
    numbers = _numbers_in_view(_cho_duyet())

    def co(raw: str) -> bool:
        whole, parts = _number_keys(raw)
        return whole in numbers or bool(parts and all(p in numbers for p in parts))

    assert co("10:00"), f"giờ khách vừa nói vẫn không hợp lệ: {sorted(numbers)}"
    assert co("2026-08-27")
    assert not co("03:17")


@pytest.mark.parametrize(
    "cau",
    [
        "Cả 2 bước đã được gửi đi.",
        "Đang chờ đơn vị cung cấp dịch vụ xác nhận.",
    ],
)
def test_answers_that_worked_before_still_work(cau):
    assert _reject_reason(AgentReply(answer=cau, suggestions=[]), _cho_duyet()) is None
