"""Luật biển số ở biểu mẫu phải khớp luật ở backend.

Hai nơi giữ cùng một luật thì sớm muộn lệch nhau, và lệch theo hướng nào cũng
tệ — nhưng khác nhau:

  * biểu mẫu DỄ hơn backend  → người dùng gửi đi, chờ, rồi nhận một câu từ chối
    ở khung chat cho một ô họ đang nhìn. Đo được: nhập "50A-82812312" (8 chữ
    số), biểu mẫu cho qua, backend trả "Vui lòng nhập biển số xe" — câu ấy nói
    họ BỎ TRỐNG ô, nên họ nhập lại y hệt.
  * biểu mẫu KHẮT KHE hơn    → mất lựa chọn hợp lệ, phiền nhưng không bế tắc.

Test này đối chiếu hai bên trên cùng một bộ mẫu.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.api.routes import _extract_plate_number

_FORMS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "serviceForms.ts"

# Biển thật, và các cách gõ sai thường gặp.
_MAU = [
    ("59A-12345", True),
    ("30A-123.45", True),
    ("51F 6789", True),
    ("29AB-1234", True),
    ("50A-82812312", False),   # 8 chữ số — chính ca người dùng gặp
    ("A-12345", False),        # thiếu số đầu
    ("5912345", False),        # không có chữ cái
    ("59A-12", False),         # quá ngắn
    ("", False),
]


def _frontend_pattern() -> re.Pattern[str]:
    source = _FORMS.read_text(encoding="utf-8")
    match = re.search(r"pattern:\s*/\^(.+?)\$/", source)
    assert match, "biểu mẫu không còn luật định dạng cho biển số"
    # `\d`, `{n,m}` giống nhau ở JS và Python; chỉ cần bỏ escape của dấu `/`.
    return re.compile("^" + match.group(1).replace("\\/", "/") + "$")


@pytest.mark.parametrize("bien,hop_le", _MAU)
def test_the_backend_and_the_form_agree(bien: str, hop_le: bool) -> None:
    backend = _extract_plate_number(bien) is not None
    frontend = _frontend_pattern().match(bien) is not None
    assert backend == hop_le, f"backend nhận sai {bien!r}: {backend}"
    assert frontend == hop_le, f"biểu mẫu nhận sai {bien!r}: {frontend}"


def test_the_rejection_message_does_not_claim_the_field_is_empty() -> None:
    """Người dùng ĐÃ nhập; nói họ chưa nhập thì họ nhập lại y hệt."""
    from src.api.routes import _FOLLOW_UP_VALIDATION_MESSAGES

    message = _FOLLOW_UP_VALIDATION_MESSAGES["plate_number"]
    assert "Vui lòng nhập biển số xe," not in message, "câu vẫn nói người dùng bỏ trống ô"
    assert "định dạng" in message, "câu không nói vấn đề thật là định dạng"
    assert "59A-12345" in message, "mất ví dụ — người dùng không có gì để đối chiếu"


def test_a_plate_written_with_a_dot_is_not_truncated() -> None:
    """Biển Việt Nam viết `30A-123.45`. Mẫu cũ khớp phần trước dấu chấm rồi
    dừng, và vì tìm thấy MỘT kết quả nên không lỗi nào được nêu:

        30A-123.45  →  30A-123     ← mất hai chữ số cuối

    Xe được đăng ký dưới một biển KHÁC biển người dùng gõ. Đây là ví dụ mẫu in
    sẵn trong ô nhập của chính ứng dụng.
    """
    assert _extract_plate_number("30A-123.45") == "30A-12345"
    assert _extract_plate_number("51F-678.90") == "51F-67890"
    # Viết liền hay có dấu đều ra một kết quả — nếu không, cùng một chiếc xe
    # thành hai bản ghi khác nhau.
    assert _extract_plate_number("30A-123.45") == _extract_plate_number("30A-12345")


def test_the_form_explains_the_format_at_the_field() -> None:
    source = _FORMS.read_text(encoding="utf-8")
    assert "patternHint" in source, "sai luật mà không có câu chỉ dẫn ngay tại ô"
