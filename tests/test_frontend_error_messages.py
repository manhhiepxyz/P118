"""Câu lỗi backend viết cho người dùng phải tới được người dùng.

Sự cố thật: người dùng gõ "vinhome sài gòn park", API trả 422 kèm đúng lý do,
và màn hình hiện "Đã có lỗi xảy ra. Vui lòng thử lại."

Nguyên nhân: frontend có `SAFE_VALIDATION_MESSAGES` — danh sách tiền tố được
phép hiện nguyên văn, để không dội văn bản tuỳ ý của server ra màn hình. Danh
sách đó đúng về nguyên tắc nhưng rệu rã lặng lẽ: backend đổi câu, frontend
không biết, và câu tử tế nhất bị thay bằng câu vô dụng nhất.

Hai bảng nói về cùng một luật thì phải có một chỗ bắt lúc chúng lệch nhau.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from src.api.routes import _FOLLOW_UP_VALIDATION_MESSAGES

_AGENT_API = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "agentApi.ts"


def _frontend_prefixes() -> list[str]:
    source = _AGENT_API.read_text(encoding="utf-8")
    block = source.split("const SAFE_VALIDATION_MESSAGES = [", 1)[1].split("]", 1)[0]
    return re.findall(r"'([^']+)'", block)


def test_the_frontend_allowlist_is_readable():
    assert _frontend_prefixes(), "không đọc được SAFE_VALIDATION_MESSAGES"


@pytest.mark.parametrize("field", sorted(_FOLLOW_UP_VALIDATION_MESSAGES))
def test_every_follow_up_message_survives_the_frontend_filter(field):
    """Câu nào backend gửi được thì frontend phải hiện được."""
    message = _FOLLOW_UP_VALIDATION_MESSAGES[field]
    prefixes = _frontend_prefixes()
    assert any(message.startswith(prefix) for prefix in prefixes), (
        f"{field}: “{message}” bị frontend nuốt, người dùng sẽ thấy câu chung chung"
    )


def test_the_unsupported_project_message_survives_too():
    from src.api.routes import _UNSUPPORTED_PROJECT_MESSAGE

    prefixes = _frontend_prefixes()
    assert any(_UNSUPPORTED_PROJECT_MESSAGE.startswith(prefix) for prefix in prefixes)


def test_the_project_message_names_the_projects():
    """Trong hội thoại không có bảng nào để nhìn — không liệt kê thì phải đoán."""
    from src.api.routes import _UNSUPPORTED_PROJECT_MESSAGE
    from src.common.projects import PROJECTS

    for project in PROJECTS:
        assert project["project_name"] in _UNSUPPORTED_PROJECT_MESSAGE


# ---------------------------------------------------------------------------
# Alias thiếu "s" — lỗi gõ phổ biến nhất với bộ tên này
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("index", range(7))
def test_every_project_also_answers_to_the_singular_spelling(index):
    """ "Vinhome" thiếu "s" phải nhận ra được, cho MỌI dự án.

    Trước đây chỉ Ocean Park có alias này, sáu dự án còn lại thì không — nên
    cùng một kiểu gõ sai lúc chạy được lúc không, và người dùng không có cách
    nào biết vì sao.
    """
    from src.common.projects import PROJECTS, resolve_project_id

    project = PROJECTS[index]
    singular = project["project_name"].replace("Vinhomes ", "Vinhome ", 1)
    assert resolve_project_id(singular) == project["project_id"], singular


def test_a_name_nobody_offered_is_still_refused():
    """Alias không được biến thành khớp gần đúng — hệ thống không đoán hộ."""
    from src.common.projects import resolve_project_id

    for junk in ["abcd", "vinhome", "vinhomes", "vin sài gòn", "sài gòn park"]:
        assert resolve_project_id(junk) is None, junk
