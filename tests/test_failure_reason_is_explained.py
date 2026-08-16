"""Bước hỏng phải nói RÕ vì sao, và đừng bảo thử lại khi thử lại vô ích.

Ảnh chụp màn hình thật:

    ⊗ Đặt lịch tham quan
      Không thể hoàn thành bước "Đặt lịch tham quan". Vui lòng thử lại.

Trong khi provider đã trả về đúng lý do, và nó nằm sẵn trong
`workflow_tasks.error_message`:

    Không có dự án 'Vinhomes Sài Gòn Park' trong danh mục.

Lý do bị mất qua hai chỗ:

  1. `TourConnector` không có `PROJECT_NOT_FOUND` trong bảng map, nên nó rơi
     xuống `UNKNOWN_EXTERNAL_ERROR`.
  2. `task_failure_message` không có nhánh cho mã đó, nên rơi xuống câu chung.

Hệ quả tệ nhất không phải là câu vô nghĩa, mà là hai chữ "thử lại": dự án không
tồn tại thì bấm lại bao nhiêu lần cũng thế, và người dùng sẽ bấm.
"""

from __future__ import annotations

import pytest

from src.common.enums import ErrorCode
from src.common.failure_messages import task_failure_message


class _Task:
    """Đủ hình dạng mà `task_failure_message` cần: `.tool` và `.input`."""

    def __init__(self, tool: str, **inputs) -> None:
        self.tool = tool
        self.input = inputs


# Mã provider thật sự phát ra, đọc từ `src/services/mock/*.py`.
PROVIDER_CODES = [
    "PROJECT_NOT_FOUND",
    "VIEWING_ALREADY_BOOKED",
    "VIEWING_SLOT_NOT_FOUND",
    "INTEREST_ALREADY_EXISTS",
]


# ---------------------------------------------------------------------------
# Connector: mã provider phải được nhận diện, không gộp vào "không rõ"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["PROJECT_NOT_FOUND", "VIEWING_ALREADY_BOOKED", "VIEWING_SLOT_NOT_FOUND"])
def test_the_tour_connector_recognises_real_provider_codes(code):
    from src.connectors.tour import TourConnector

    mapped = TourConnector(base_url="http://tour")._map_error_code(code)
    assert mapped != ErrorCode.UNKNOWN_EXTERNAL_ERROR, f"{code} vẫn bị gộp vào 'không rõ'"


def test_the_consultation_connector_recognises_a_missing_project():
    from src.connectors.consultation import ConsultationConnector

    mapped = ConsultationConnector(base_url="http://consultation")._map_error_code("PROJECT_NOT_FOUND")
    assert mapped != ErrorCode.UNKNOWN_EXTERNAL_ERROR


# ---------------------------------------------------------------------------
# Thông báo: nói lý do, và nói việc cần làm
# ---------------------------------------------------------------------------


def test_a_missing_project_says_which_project_and_what_to_do():
    message = task_failure_message(
        _Task("schedule_property_viewing", project_name="Vinhomes Sài Gòn Park"),
        "Đặt lịch tham quan",
        ErrorCode.PROJECT_NOT_FOUND,
    )
    assert "Vinhomes Sài Gòn Park" in message, message
    assert "thử lại" not in message.lower(), "bảo thử lại một việc không bao giờ chạy được"
    assert "chọn" in message.lower(), "không nói người dùng cần làm gì"


def test_a_slot_already_booked_says_so():
    message = task_failure_message(
        _Task("schedule_property_viewing", viewing_date="2026-08-22", viewing_time="10:30"),
        "Đặt lịch tham quan",
        ErrorCode.VIEWING_ALREADY_BOOKED,
    )
    assert "10:30" in message or "2026-08-22" in message, message
    assert "đã" in message.lower()


def test_a_duplicate_interest_says_so():
    message = task_failure_message(
        _Task("register_property_interest", project_name="Vinhomes Ocean Park"),
        "Đăng ký nhận tư vấn",
        ErrorCode.INTEREST_ALREADY_EXISTS,
    )
    assert "Vinhomes Ocean Park" in message
    assert "thử lại" not in message.lower()


# ---------------------------------------------------------------------------
# Câu dự phòng: đừng hứa điều không đúng
# ---------------------------------------------------------------------------


def test_the_generic_message_does_not_promise_that_retrying_helps():
    """Câu cuối cùng dùng cho mã CHƯA biết, nên nó không được khẳng định gì.

    "Vui lòng thử lại" là một lời hứa: rằng lần sau sẽ khác. Với một mã lỗi
    chưa ai phân loại, ta không biết điều đó có đúng không.
    """
    message = task_failure_message(_Task("schedule_property_viewing"), "Đặt lịch tham quan", "MOT_MA_LA")
    assert "thử lại" not in message.lower(), message
    assert "Đặt lịch tham quan" in message


def test_a_retryable_infrastructure_error_may_suggest_retrying():
    """Ngược lại: dịch vụ tạm gián đoạn thì thử lại đúng là việc nên làm."""
    message = task_failure_message(
        _Task("schedule_property_viewing"), "Đặt lịch tham quan", ErrorCode.SERVICE_UNAVAILABLE
    )
    assert "thử lại" in message.lower(), message


# ---------------------------------------------------------------------------
# Không rò rỉ
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [*PROVIDER_CODES, "SERVICE_UNAVAILABLE", "MOT_MA_LA"])
def test_no_message_leaks_internal_vocabulary(code):
    message = task_failure_message(
        _Task("schedule_property_viewing", project_id="PRJ-007", project_name="Vinhomes Ocean Park"),
        "Đặt lịch tham quan",
        code,
    )
    for leaked in ("PRJ-007", "schedule_property_viewing", "Traceback", "http", "_"):
        assert leaked not in message, f"{code}: rò {leaked!r} → {message}"


def test_both_project_not_found_paths_say_the_same_thing():
    """Hai đường cùng dẫn tới "dự án không có" thì phải nói giống nhau.

    Trước đây nhánh hỏi-bổ-sung liệt kê 7 dự án, còn nhánh bước-hỏng chỉ bảo
    "chọn trong danh sách được hỗ trợ" — mà trong hội thoại thì không có danh
    sách nào để nhìn. Người dùng gặp đường nào là may rủi, và đường kém hơn để
    họ đoán tiếp.
    """
    from src.api.routes import _UNSUPPORTED_PROJECT_MESSAGE
    from src.common.projects import PROJECTS

    failure = task_failure_message(
        _Task("schedule_property_viewing", project_name="Vinhomes Sky Garden"),
        "Đặt lịch tham quan",
        ErrorCode.PROJECT_NOT_FOUND,
    )
    for project in PROJECTS:
        assert project["project_name"] in failure, f"{project['project_name']} thiếu ở nhánh bước-hỏng"
        assert project["project_name"] in _UNSUPPORTED_PROJECT_MESSAGE, "thiếu ở nhánh hỏi-bổ-sung"


def test_it_names_the_project_the_user_actually_typed():
    """Input thật mang tên ở `project_id`, không phải `project_name`.

    Đọc từ DB lúc sự cố:

        {"project_id": "Vinhomes Sky Garden", "viewing_date": ...}

    Planner điền TÊN vào `project_id`, và Validator chỉ đổi sang mã khi tên có
    trong danh mục — đúng những lần hỏng thì nó không đổi được. Chỉ đọc
    `project_name` nên câu trả lời thành "Dự án đã chọn…", không nói được cái
    tên người dùng vừa gõ.
    """
    message = task_failure_message(
        _Task("schedule_property_viewing", project_id="Vinhomes Sky Garden"),
        "Đặt lịch tham quan",
        ErrorCode.PROJECT_NOT_FOUND,
    )
    assert "Vinhomes Sky Garden" in message, message


def test_the_internal_project_code_is_never_shown():
    """`PRJ-007` là mã nội bộ — người dùng không gõ nó và không cần biết nó."""
    message = task_failure_message(
        _Task("schedule_property_viewing", project_id="PRJ-999"),
        "Đặt lịch tham quan",
        ErrorCode.PROJECT_NOT_FOUND,
    )
    assert "PRJ-999" not in message
    assert "Dự án đã chọn" in message
