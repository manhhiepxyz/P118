"""Mỗi mã lỗi phải có câu của riêng nó.

Người dùng hỏi thẳng: "nó chưa hoàn thành là do lỗi gì? xe đã đăng ký hay hết
chỗ?" — và họ hỏi vì màn hình không nói được.

Đo được: backend định nghĩa 25 mã lỗi, giao diện chỉ có câu cho 3. Hai mươi hai
mã còn lại rơi vào cùng một câu "Yêu cầu này dừng giữa chừng.", nên:

    BOOKING_ALREADY_EXISTS   xe đã có chỗ → KHÔNG cần làm gì
    NO_AVAILABILITY          khu hết chỗ  → PHẢI đổi khu hoặc ngày

hiện ra y hệt nhau, dù hai việc cần làm ngược nhau.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.common.enums import ErrorCode

_STATUS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "status.ts"


def _failure_text() -> dict[str, str]:
    source = _STATUS.read_text(encoding="utf-8")
    start = source.index("const FAILURE_TEXT")
    block = source[start : source.index("\n}", start)]
    return dict(re.findall(r"^\s*([A-Z_]+): '([^']+)'", block, re.M))


@pytest.mark.parametrize("code", sorted(c.value for c in ErrorCode))
def test_every_backend_code_has_user_facing_text(code: str) -> None:
    assert code in _failure_text(), (
        f"{code} không có câu nào — nó sẽ hiện ra giống hệt mọi lỗi khác, và người dùng không biết phải làm gì"
    )


def test_the_two_parking_failures_do_not_read_the_same() -> None:
    """Hai lỗi này cần hai hành động NGƯỢC nhau."""
    texts = _failure_text()
    da_co = texts["BOOKING_ALREADY_EXISTS"]
    het_cho = texts["NO_AVAILABILITY"]

    assert da_co != het_cho
    assert "vẫn được giữ" in da_co, "không nói rằng chỗ đỗ còn nguyên, không cần làm gì"
    assert "hết chỗ" in het_cho, "không nói rằng khu đã hết chỗ"
    assert "chọn ngày hoặc khu khác" in het_cho, "không chỉ ra việc cần làm"


def test_the_raw_provider_message_is_not_shown_first() -> None:
    """`error_message` là câu THÔ của provider, và provider nói tiếng Anh.

    Đo được: "Vehicle already booked for that date" — ưu tiên nó nghĩa là đẩy
    nguyên văn tiếng Anh ra trước mặt khách hàng. Câu tiếng Việt đúng đã có
    sẵn ở `message`, do backend dựng kèm tên bước.
    """
    source = _STATUS.read_text(encoding="utf-8")
    uu_tien = source.index("if (task.message) return task.message")
    tho = source.index("if (task.error_message) return task.error_message")
    assert uu_tien < tho, "câu thô của provider được ưu tiên hơn câu backend đã dựng cho người đọc"
