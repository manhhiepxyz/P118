"""Đơn vị từ chối một dịch vụ thì câu trả lời KHÔNG được nói "mọi thứ đã sẵn sàng".

Owner: Thành Bảo (Decision layer)
File: tests/test_a_refusal_is_never_summarised_away.py

NGUYÊN VĂN, workflow 4956721e trên stack demo:

    service_approvals T1  REJECTED · OTHER
                          "Chưa có nhân viên tư vấn khung giờ này"
    workflow_tasks    T1  CANCELLED

    P-118: "Mọi thứ đã sẵn sàng trừ một bước cuối: bạn cần xác nhận thanh
            toán 150.000 VND cho chỗ đỗ xe Khu A."

Bốn dịch vụ kia chạy được và độc lập với T1, nên chúng chạy tiếp — phần đó
ĐÚNG. Sai là câu tổng kết: một dịch vụ vừa bị từ chối, và người dùng được mời
trả tiền với lời đảm bảo mọi thứ đã sẵn sàng.

Backend lúc ấy ĐÃ đưa lý do từ chối vào dữ kiện (`_refused_services` trong
`_facts_for`, container khởi động 04:16, workflow 04:49). Tầng nói có dữ liệu
và vẫn bỏ qua nó. Đưa thêm ngữ cảnh chỉ GIẢM khả năng bỏ sót; muốn hết thì
phải cưỡng chế.

Nên đây là guard, cùng khuôn với `view.next_step`: "Có mặt thì câu trả lời BẮT
BUỘC nhắc tới — guard sẽ loại nếu thiếu."
"""

from __future__ import annotations

import pytest

from src.agents.response_agent import AgentReply, ReplyView, _reject_reason

# `150.000` phải có mặt trong dữ kiện, nếu không guard SỐ chặn trước và bài kiểm
# này xanh/đỏ vì một lý do khác hẳn thứ nó định canh.
_TIEN = "\n\n## Các bước của yêu cầu này\n- Thanh toán phí [WAITING_APPROVAL] — Số tiền: 150.000 VND"

TU_CHOI = {
    "su_that_hien_co": (
        "## Đơn vị đã từ chối\n"
        "- Đăng ký nhận tư vấn: Chưa có nhân viên tư vấn khung giờ này (OTHER)" + _TIEN
    )
}


def _view(**kw) -> ReplyView:
    base = dict(goal="đăng ký tư vấn và giữ chỗ đỗ xe", status="WAITING_APPROVAL",
                baseline_message="", steps=[], facts=TU_CHOI)
    base.update(kw)
    return ReplyView(**base)


def _reply(answer: str) -> AgentReply:
    return AgentReply(answer=answer, suggestions=[])


# ĐÂY LÀ CÂU ĐÃ HIỆN RA CHO NGƯỜI DÙNG.
def test_the_answer_that_hid_the_refusal_is_rejected() -> None:
    ly_do = _reject_reason(
        _reply(
            "Mọi thứ đã sẵn sàng trừ một bước cuối: bạn cần xác nhận thanh toán "
            "150.000 VND cho chỗ đỗ xe Khu A. Bạn xác nhận thanh toán nhé?"
        ),
        _view(),
    )
    assert ly_do is not None, "câu giấu mất lời từ chối vẫn lọt qua guard"


def test_an_answer_that_says_the_refusal_passes() -> None:
    """Hàng rào: guard không được cản đường NÓI THẬT."""
    ly_do = _reject_reason(
        _reply(
            "Đăng ký nhận tư vấn bị đơn vị từ chối: chưa có nhân viên tư vấn khung "
            "giờ này. Còn khoản 150.000 VND cho chỗ đỗ xe đang chờ bạn xác nhận "
            "thanh toán."
        ),
        _view(),
    )
    assert ly_do is None, ly_do


def test_naming_the_refused_service_is_enough() -> None:
    """Không đòi chép nguyên văn lý do — nêu đúng dịch vụ bị từ chối là đủ."""
    ly_do = _reject_reason(
        _reply(
            "Đơn vị chưa nhận Đăng ký nhận tư vấn lần này. Khoản 150.000 VND cho "
            "chỗ đỗ xe đang chờ bạn xác nhận thanh toán."
        ),
        _view(),
    )
    assert ly_do is None, ly_do


# Không có từ chối thì guard phải im. Một guard bật nhầm sẽ đẩy MỌI câu trả lời
# về bản dự phòng, và người dùng nhận đúng một câu cho mọi tình huống.
@pytest.mark.parametrize(
    "facts",
    [None, {}, {"su_that_hien_co": "## Dự án\n- Vinhomes Pearl Bay"}],
)
def test_without_a_refusal_the_guard_stays_quiet(facts) -> None:
    ly_do = _reject_reason(
        _reply("Khoản phí chỗ đỗ xe Khu A đang chờ bạn xác nhận thanh toán nhé."),
        _view(facts=facts),
    )
    assert ly_do is None, ly_do


# HAI dịch vụ bị từ chối, câu trả lời chỉ nhắc một.
#
# Không có ca này thì luật "nêu ít nhất một" vẫn xanh — và người dùng đọc xong
# tin rằng chỉ một thứ hỏng, trong khi có hai.
def test_mentioning_one_refusal_does_not_cover_the_other() -> None:
    hai = {
        "su_that_hien_co": (
            "## Đơn vị đã từ chối\n"
            "- Đăng ký nhận tư vấn: Chưa có nhân viên tư vấn khung giờ này (OTHER)\n"
            "- Đặt xe đưa đón: Hết xe khung giờ này (NO_AVAILABILITY)" + _TIEN
        )
    }
    ly_do = _reject_reason(
        _reply(
            "Đăng ký nhận tư vấn bị đơn vị từ chối. Khoản 150.000 VND cho chỗ đỗ xe "
            "đang chờ bạn xác nhận thanh toán."
        ),
        _view(facts=hai),
    )
    assert ly_do is not None, "nhắc một lời từ chối rồi bỏ qua lời còn lại vẫn lọt"
