"""Guard tiền không được đọc nhầm chữ tiếng Việt thành số tiền.

`đ` viết tắt của "đồng", nhưng nó cũng là chữ cái mở đầu vô số từ thường gặp:
đã, đến, được, đó, đơn, đợt. Mẫu cũ không có ranh giới từ, nên `"ngày 25/09
đến"` khớp `"09 đ"`.

Thiệt hại KHÔNG nằm ở guard mà ở câu trả lời. Guard loại câu, tầng trả lời lùi
về câu deterministic, và người dùng đọc "Mình đã trả lời bạn ở trên." cho một
câu hỏi chưa hề được trả lời. Đo được: 3/3 lượt gọi liên tiếp bị loại với lý do
"nêu số tiền như đã trả trong khi chưa thanh toán", cho một câu hỏi ĐƯỜNG ĐI
không nhắc tới tiền.

Bất kỳ câu nào có một con số rồi tới một từ bắt đầu bằng `đ` đều dính — mà
ngày, giờ và số lượng thì luôn có số, còn "đã/đến/được" thì có ở khắp nơi.
"""

from __future__ import annotations

import pytest

from src.agents.response_agent import _MONEY

# Câu bình thường: có số, có từ bắt đầu bằng `đ`, KHÔNG có tiền.
_KHONG_PHAI_TIEN = [
    "Lịch tham quan lúc 10:00 ngày 25/09 đã được gửi.",
    "Mình đã gửi yêu cầu ngày 25/09 đến đơn vị phụ trách.",
    "Khu A có 3 đợt tham quan trong ngày.",
    "Xe 7 chỗ đón bạn lúc 8:00 đúng giờ nhé.",
    "Bạn đã đặt 2 địa điểm rồi đó.",
]

# Tiền thật, ở mọi cách viết mà hệ thống dùng.
_LA_TIEN = [
    "Phí cần thanh toán: 150.000 VND.",
    "Phí là 100.000đ.",
    "Tổng cộng 150.000 đồng.",
    "Số tiền 200.000₫ đã được ghi nhận.",
    "Khoản phí 100.000 vnđ.",
]


@pytest.mark.parametrize("cau", _KHONG_PHAI_TIEN)
def test_a_normal_sentence_is_not_read_as_money(cau: str) -> None:
    assert _MONEY.search(cau.casefold()) is None, (
        f"đọc nhầm thành số tiền: {cau!r} — câu trả lời đúng sẽ bị loại và "
        "người dùng nhận câu mặc định thay cho câu trả lời"
    )


@pytest.mark.parametrize("cau", _LA_TIEN)
def test_real_money_is_still_caught(cau: str) -> None:
    assert _MONEY.search(cau.casefold()) is not None, (
        f"bỏ lọt số tiền: {cau!r} — nới guard mà làm mất chính thứ nó canh thì "
        "người dùng lại đọc được 'thành công (phí 150.000 VND)' cho một khoản "
        "chưa hề trả"
    )
