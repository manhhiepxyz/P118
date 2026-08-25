"""Người duyệt phải thấy HẬU QUẢ của mã mình chọn, ngay lúc chọn.

Owner: Thành Bảo (Decision layer)
File: tests/test_the_provider_sees_what_the_code_does.py

Backend đọc MÃ từ chối để quyết định khách có được sửa hay không:

    NO_AVAILABILITY      → mở lượt sửa, khách chọn lại ngày/khu
    INVALID_REQUEST      → dừng hẳn
    SERVICE_UNAVAILABLE  → dừng hẳn
    OTHER                → dừng hẳn

Nhưng giao diện duyệt chỉ nói hậu quả cho MỘT mã (`NO_AVAILABILITY`). Ba mã kia
im lặng, nên người duyệt đọc danh sách như bốn cái nhãn phân loại — không biết
mình đang quyết định khách có lối đi tiếp hay không.

ĐO ĐƯỢC trên `service_approvals`, 12 lượt từ chối thật:

    NO_AVAILABILITY ×9   "hết chỗ / không còn lịch trống"          mã khớp
    OTHER                "Chưa có nhân viên tư vấn khung giờ này"  lý do là HẾT CHỖ
    SERVICE_UNAVAILABLE  "Yêu cầu đổi thời gian"                   lý do là XIN ĐỔI GIỜ
    OTHER                "Lịch bận"                                lý do là HẾT CHỖ

Ba lượt lệch. Lượt `SERVICE_UNAVAILABLE` rõ nhất: người duyệt VIẾT THẲNG là muốn
đổi giờ, mà mã lại nói dịch vụ ngừng — nên khách bị dừng hẳn, không ai mời họ
đổi giờ cả.

Không phải bấm nhầm: ô chọn không có giá trị mặc định và không chọn thì không
gửi được. Họ chủ động chọn, chỉ là không biết mã ấy làm gì.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.orchestration.service_approval import REJECT_CODES

_PAGE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "ProviderReviewPage.tsx"


def _nguon() -> str:
    return _PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("code", REJECT_CODES)
def test_every_code_states_its_consequence(code: str) -> None:
    """Mọi mã đều phải có một câu nói ra hậu quả, không riêng `NO_AVAILABILITY`."""
    source = _nguon()
    khoi = re.search(r"REJECT_CONSEQUENCE[^=]*=\s*\{(.+?)\n\}", source, re.DOTALL)
    assert khoi, "giao diện duyệt chưa có bảng hậu quả cho mã từ chối"
    assert f"{code}:" in khoi.group(1) or f"'{code}'" in khoi.group(1), (
        f"mã {code} không nói cho người duyệt biết nó sẽ gây ra chuyện gì"
    )


def test_the_repairable_code_matches_the_backend() -> None:
    """Một luật, hai nơi — kiểm chúng không trôi khỏi nhau.

    Nếu backend đổi mã nào là "sửa được" mà giao diện không đổi theo, người
    duyệt đọc một lời hứa sai: họ tưởng khách được mời chọn lại, còn hệ thống
    thì dừng hẳn.
    """
    from src.orchestration.demo_service import _REPAIRABLE_REJECT_CODE

    source = _nguon()
    khoi = re.search(r"REJECT_CONSEQUENCE[^=]*=\s*\{(.+?)\n\}", source, re.DOTALL)
    assert khoi, "giao diện duyệt chưa có bảng hậu quả"
    text = khoi.group(1)

    # Đúng MỘT mã được mô tả là mở lượt sửa, và nó phải là mã backend dùng.
    dong_sua = [d for d in text.splitlines() if "chọn lại" in d]
    assert len(dong_sua) == 1, f"có {len(dong_sua)} mã hứa cho khách sửa, backend chỉ nhận một"
    assert _REPAIRABLE_REJECT_CODE in dong_sua[0], f"giao diện hứa cho sửa ở một mã khác {_REPAIRABLE_REJECT_CODE!r}"


def test_the_consequence_is_shown_for_whatever_is_selected() -> None:
    """Hiện theo mã ĐANG chọn, không phải chỉ hiện cho một mã.

    Bản trước gắn cứng `rejectTarget.code === 'NO_AVAILABILITY'`, nên ba mã còn
    lại không hiện gì — đúng chỗ người duyệt cần biết nhất.
    """
    source = _nguon()
    # Cấm cái CHẶN HIỂN THỊ, không cấm cái tô màu.
    #
    # So sánh với một mã cụ thể để đổi màu chữ (cảnh báo vàng cho "dừng hẳn")
    # là hợp lệ và hữu ích. Cấm cả chuỗi thì bài kiểm này bắt luôn thứ nó không
    # định canh, và người sau sẽ gỡ màu thay vì gỡ lỗi.
    assert "{rejectTarget.code === 'NO_AVAILABILITY' && (" not in source, (
        "câu hậu quả vẫn gắn cứng vào một mã; ba mã kia im lặng"
    )
    assert "REJECT_CONSEQUENCE[" in source, "chưa tra bảng hậu quả theo mã đang chọn"
