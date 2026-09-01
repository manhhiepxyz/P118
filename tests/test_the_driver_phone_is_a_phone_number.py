"""Ô "Số điện thoại cho tài xế" phải là một số điện thoại.

Owner: Thành Bảo (Decision layer)
File: tests/test_the_driver_phone_is_a_phone_number.py

LỖI ĐÃ BÁO: "tôi nhập bừa chữ, số vào ô Số điện thoại cho tài xế vẫn được".

Nguyên nhân nằm ở `missingFields` trong `frontend/src/lib/serviceForms.ts`:

    if (field.freeText) return false

`freeText` đánh dấu "giá trị này chảy vào CÂU gửi Planner, không vào ô của một
tool" — đó là chuyện luồng dữ liệu. Nó bị dùng nhầm thành "ô này không cần
kiểm", nên `pickup_phone` nhận mọi thứ, kể cả một dòng chữ.

Hậu quả không dừng ở giao diện: số ấy đi vào câu gửi Planner rồi tới đơn vị vận
chuyển, và tài xế nhận một số không gọi được. Người dùng đứng ở điểm đón chờ
một cuộc gọi không bao giờ tới.

Backend đã có luật số điện thoại (`src/models/schemas.py`). Hai nơi giữ cùng một
luật thì sớm muộn lệch nhau — file này đối chiếu chúng, đúng khuôn
`tests/test_plate_rule_matches_backend.py` đã làm cho biển số.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_FORMS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "serviceForms.ts"

# Số thật người Việt gõ, và các cách gõ sai thường gặp.
_MAU = [
    ("0901234567", True),
    ("0948838627", True),
    ("+84901234567", True),
    ("090 123 4567", True),
    ("abc", False),
    ("khong biet", False),
    ("0901234567 gọi giúp", False),
    ("123", False),  # quá ngắn
    ("09012345678901234", False),  # quá dài
    ("", False),
]


def _luat_bieu_mau() -> re.Pattern[str]:
    source = _FORMS.read_text(encoding="utf-8")
    khoi = re.search(r"key:\s*'pickup_phone'.*?\n    \}", source, re.DOTALL)
    assert khoi, "không tìm thấy ô `pickup_phone` trong biểu mẫu"
    mau = re.search(r"pattern:\s*/\^(.+?)\$/", khoi.group(0))
    assert mau, "ô số điện thoại cho tài xế KHÔNG có luật định dạng — nhập gì cũng nhận"
    return re.compile("^" + mau.group(1).replace("\\/", "/") + "$")


@pytest.mark.parametrize("so,hop_le", _MAU)
def test_the_form_accepts_only_a_phone_number(so: str, hop_le: bool) -> None:
    assert bool(_luat_bieu_mau().fullmatch(so)) is hop_le, so


def test_the_backend_rule_agrees() -> None:
    """Cùng một luật ở hai nơi — kiểm chúng không trôi khỏi nhau."""
    from src.models.schemas import DemoContactProfile

    backend = re.compile(DemoContactProfile.model_fields["phone"].metadata[0].pattern)
    for so, hop_le in _MAU:
        assert bool(backend.fullmatch(so)) is hop_le, f"backend lệch ở {so!r}"


def test_a_free_text_field_is_still_checked_when_it_has_a_rule() -> None:
    """`freeText` nói về LUỒNG DỮ LIỆU, không phải "miễn kiểm".

    Không có phép kiểm này thì đủ để thêm `pattern` vào ô mà quên sửa
    `missingFields` — luật có mặt, và không bao giờ chạy.
    """
    source = _FORMS.read_text(encoding="utf-8")
    ham = re.search(r"if \(field\.freeText\)(.{0,400})", source, re.DOTALL)
    assert ham, "không tìm thấy nhánh `freeText` trong `missingFields`"
    assert "pattern" in ham.group(1), "nhánh `freeText` thoát ra trước khi xét `pattern` — ô có luật vẫn nhận mọi thứ"
