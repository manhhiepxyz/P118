"""Tên cũng phải đến từ dữ liệu, không chỉ con số.

Owner: Thành Bảo (Decision layer)
File: tests/test_a_name_must_come_from_the_data_too.py

`_reject_reason()` đã chặn khá kỹ: định danh nội bộ, khẳng định đã-xong-khi-chưa,
nêu tiền như đã trả, và MỌI CON SỐ phải có mặt trong view (`_numbers_in_view`).

Nhưng nó chỉ soi số. Câu đã hiện ra cho người dùng, workflow a39d6ebc:

    Bạn:    có những dự án nào
    P-118:  Hiện tại mình có các dự án: Khu A, Khu B, Khu C.
            Bạn muốn tham quan dự án nào?

"Khu A/B/C" là tên KHU ĐỖ XE. Không có con số nào sai, nên guard cho qua trọn
vẹn. Bảy dự án thật đều bắt đầu bằng "Vinhomes".

Hai luật ở đây, cố ý HẸP. `_reject_reason` có tiền sử chặn nhầm — chú thích
trong `_numbers_in_view` ghi lại 3/3 lượt liên tiếp bị loại vì model trích đúng
một con số từ câu chính nó vừa nói. Một guard hay chặn nhầm sẽ bị gỡ, và khi
đó nó không bảo vệ gì nữa. Nên:

  1. Một cái tên dạng "Vinhomes ..." phải có trong dữ liệu view.
  2. Không được giới thiệu một KHU ĐỖ XE như một DỰ ÁN.

Tập tên cho phép đọc từ CÙNG nguồn với tập số — `_flatten_facts(view.facts)` —
nên snapshot (`src/orchestration/snapshot.py`) đã đưa bảy dự án thật vào đó là
đủ, không cần một danh sách thứ hai để trôi lệch.
"""

from __future__ import annotations

from src.agents.response_agent import AgentReply, ReplyView, _reject_reason


def _view(**kwargs) -> ReplyView:
    base = dict(
        goal="có những dự án nào",
        status="CHAT",
        baseline_message="",
        steps=[],
    )
    base.update(kwargs)
    return ReplyView(**base)


def _reply(answer: str) -> AgentReply:
    return AgentReply(answer=answer, suggestions=[])


DU_LIEU = {
    "su_that_hien_co": (
        "## Dự án\n- Vinhomes Sài Gòn Park; Vinhomes Global Gate Hạ Long; "
        "Vinhomes Hải Vân Bay; Vinhomes Pearl Bay; Vinhomes Green Paradise; "
        "Vinhomes Golden City; Vinhomes Ocean Park"
    )
}


# ĐÂY LÀ CÂU ĐÃ HIỆN RA CHO NGƯỜI DÙNG.
def test_a_parking_zone_is_never_offered_as_a_project() -> None:
    ly_do = _reject_reason(
        _reply("Hiện tại mình có các dự án: Khu A, Khu B, Khu C. Bạn muốn tham quan dự án nào?"),
        _view(facts=DU_LIEU),
    )
    assert ly_do is not None, "câu bịa dự án vẫn lọt qua guard"


def test_a_project_name_that_is_not_in_the_data_is_rejected() -> None:
    """Tên nghe rất thật vẫn phải có trong dữ liệu mới được nói."""
    ly_do = _reject_reason(
        _reply("Bạn có thể tham quan Vinhomes Riverside Palace nhé."),
        _view(facts=DU_LIEU),
    )
    assert ly_do is not None


def test_the_real_project_list_passes() -> None:
    """Hàng rào quan trọng nhất: guard không được cản đường nói THẬT."""
    ly_do = _reject_reason(
        _reply(
            "Hiện mình hỗ trợ các dự án: Vinhomes Sài Gòn Park; Vinhomes Global Gate Hạ Long; "
            "Vinhomes Hải Vân Bay; Vinhomes Pearl Bay; Vinhomes Green Paradise; "
            "Vinhomes Golden City; Vinhomes Ocean Park."
        ),
        _view(facts=DU_LIEU),
    )
    assert ly_do is None, ly_do


def test_naming_one_real_project_passes() -> None:
    ly_do = _reject_reason(
        _reply("Lịch tham quan Vinhomes Pearl Bay đã được ghi nhận."),
        _view(facts=DU_LIEU),
    )
    assert ly_do is None, ly_do


# Khu đỗ xe là chuyện BÌNH THƯỜNG khi nói về chỗ đỗ xe. Chỉ cấm khi nó được
# giới thiệu như một dự án. Chặn cả hai là làm hỏng luồng đặt chỗ đỗ xe.
def test_talking_about_a_parking_zone_as_a_parking_zone_is_fine() -> None:
    ly_do = _reject_reason(
        _reply("Chỗ đỗ xe Khu A đã được giữ cho bạn."),
        _view(status="SUCCESS", facts=DU_LIEU),
    )
    assert ly_do is None, ly_do


def test_a_zone_in_a_different_sentence_from_the_word_project_is_fine() -> None:
    """Chỉ chặn khi khu đỗ xe được nêu NHƯ một dự án, không phải khi cùng đoạn."""
    ly_do = _reject_reason(
        _reply("Lịch tham quan Vinhomes Pearl Bay đã chốt. Chỗ đỗ xe Khu A cũng đã giữ xong."),
        _view(status="SUCCESS", facts=DU_LIEU),
    )
    assert ly_do is None, ly_do


def test_without_any_data_a_project_name_is_still_rejected() -> None:
    """Không có dữ liệu thì không được nêu tên dự án nào cả."""
    ly_do = _reject_reason(_reply("Mời bạn xem Vinhomes Pearl Bay."), _view())
    assert ly_do is not None


# Luật khu-đỗ-xe xét theo TỪNG CÂU. Không có ca này thì đổi sang xét cả đoạn
# vẫn xanh — và xét cả đoạn sẽ chặn một câu hoàn toàn bình thường.
def test_a_project_sentence_and_a_zone_sentence_can_live_together() -> None:
    ly_do = _reject_reason(
        _reply("Lịch tham quan dự án Vinhomes Pearl Bay đã chốt. Chỗ đỗ xe Khu A cũng đã giữ xong."),
        _view(status="SUCCESS", facts=DU_LIEU),
    )
    assert ly_do is None, ly_do


# Người dùng gõ tên gì KHÔNG làm dự án đó tồn tại.
#
# Đây là chỗ luật tên khác luật số: nhắc lại một con số khách vừa gõ là việc
# trợ lý phải làm, còn khẳng định một dự án có thật vì họ đã gõ tên nó thì
# không. Không có ca này thì thêm `view.goal` vào nguồn tên vẫn xanh.
def test_a_project_the_user_invented_is_not_a_source() -> None:
    ly_do = _reject_reason(
        _reply("Mình sẽ đặt lịch tham quan Vinhomes Sunset Villa cho bạn."),
        _view(goal="đặt lịch tham quan Vinhomes Sunset Villa", facts=DU_LIEU),
    )
    assert ly_do is not None, "tên do người dùng bịa được coi là có thật"


# Hai dự án nằm liền nhau trong danh sách không được dính thành một tên.
def test_two_projects_side_by_side_are_read_as_two_names() -> None:
    from src.agents.response_agent import _name_at

    text = "Vinhomes Pearl Bay; Vinhomes Ocean Park"
    assert _name_at(text, 0) == "Vinhomes Pearl Bay"
