"""Khung tạm chỉ được vẽ bước người dùng ĐÃ CHỌN.

Trong lúc Planner còn chạy, canvas vẽ các bước ĐOÁN từ dịch vụ đã chọn. Phép
đoán ấy duyệt mọi ô của dịch vụ và lấy `tool` của chúng — kể cả ô đang ẨN.

Ô ẩn là ô người dùng KHÔNG chọn. Đo được: không tích "xe đưa đón", nhưng khung
tạm vẫn vẽ "Đặt xe đưa đón" — vì `book_shuttle` khai trên một ô có `showIf`.
Người dùng nhìn thấy một bước họ vừa từ chối, rồi hỏi vì sao nó ở đó.
"""

from __future__ import annotations

import re
from pathlib import Path

_FORMS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "serviceForms.ts"
_PAGE = Path(__file__).resolve().parents[1] / "frontend" / "src" / "pages" / "JourneyWorkspacePage.tsx"


def test_hidden_fields_do_not_contribute_a_step() -> None:
    source = _FORMS.read_text(encoding="utf-8")
    body = source[source.index("export function expectedTools(") :]
    body = body[: body.index("\n}")]
    assert "field.showIf" in body, (
        "phép đoán bước đọc mọi ô bất kể điều kiện — nó sẽ vẽ bước của một ô "
        "người dùng chưa mở"
    )
    assert "continue" in body, "có kiểm điều kiện nhưng không bỏ qua ô ẩn"


def test_the_page_passes_what_the_user_actually_filled() -> None:
    """Có kiểm điều kiện mà không truyền giá trị thì điều kiện luôn sai."""
    page = _PAGE.read_text(encoding="utf-8")
    assert re.search(r"expectedTools\(picked,\s*values\)", page), (
        "gọi phép đoán mà không đưa lựa chọn của người dùng — mọi ô có điều "
        "kiện đều bị coi là chưa chọn, hoặc tệ hơn, được coi là đã chọn"
    )


def test_shuttle_is_declared_behind_a_condition() -> None:
    """Nếu ô xe đưa đón không còn `showIf` thì bản vá trên vô nghĩa."""
    source = _FORMS.read_text(encoding="utf-8")
    i = source.index("tool: 'book_shuttle'")
    # Soi khối khai báo của CHÍNH ô đó: `showIf` đứng SAU `tool`, nên nhìn
    # ngược lên chỉ thấy ô liền trước và test xanh/đỏ vì nhầm ô.
    khoi = source[i : source.index("},", i)]
    assert "showIf" in khoi, (
        "ô xe đưa đón không còn điều kiện hiển thị — nó sẽ luôn được đoán là có"
    )
