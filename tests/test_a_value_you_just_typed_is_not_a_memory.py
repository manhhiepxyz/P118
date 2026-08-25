"""Giá trị người dùng VỪA GÕ không phải là "lấy từ chuyện cũ".

`_fields_taken_from_recall` chặn một lỗi thật: model thấy "như lần trước" rồi
tự điền khu/ngày cũ và ĐẶT THẬT. Cửa thoát của nó là *"giá trị này có trong
chính câu người dùng vừa nói"*.

Cửa ấy so GIÁ TRỊ CANONICAL với CHỮ THÔ, và cầu nối `spoken_forms` chỉ phủ hai
loại (`ZONE_A→"khu a"`, `rent→"thuê"`). Mã dự án và ngày không có cầu nối:

    bạn gõ:      "Vinhomes Green Paradise ngày 30/08"
    model điền:  project_id="PRJ-005", viewing_date="2026-08-30"
    guard hỏi:   "PRJ-005" có trong câu bạn gõ không?  → KHÔNG
    ký ức có:    "PRJ-005"                              → CÓ
    kết luận:    lấy từ chuyện cũ → hỏi lại tên dự án

Đo được: bấm Dừng rồi gõ lại (lượt cũ vào `nho_lai`) thì bị hỏi lại đúng tên
dự án vừa gõ. Nghịch lý: **giữ nguyên field nào thì field đó bị hỏi lại** —
càng sửa ít càng bị hỏi nhiều.

Hệ thống ĐÃ CÓ bảng ánh xạ chính thức cho cả hai (`src/common/projects.py`,
bộ đọc ngày). Guard chỉ cần dùng chúng — không nới độ chặt, chỉ hiểu đúng
nghĩa "đã được nói ra".
"""

from __future__ import annotations

import pytest

from src.agents.planner import Planner
from src.common.failure_messages import spoken_forms
from src.common.task_plan import Task, TaskPlan

# Lượt trước đã bị huỷ, nằm trong nho_lai.
NHO_LAI = [
    {
        "goal": "Đặt lịch tham quan Vinhomes Green Paradise ngày 27/08 lúc 10:00",
        "project_id": "PRJ-005",
        "viewing_date": "2026-08-27",
        "viewing_time": "10:00",
    }
]


def _plan(project_id="PRJ-005", date="2026-08-30", time="10:00") -> TaskPlan:
    return TaskPlan(
        goal="x",
        tasks=[
            Task(
                task_id="T1",
                tool="schedule_property_viewing",
                depends_on=[],
                input={"project_id": project_id, "viewing_date": date, "viewing_time": time},
            )
        ],
    )


# --- cầu nối canonical ↔ lời nói ---------------------------------------------


def test_a_project_id_knows_the_name_people_actually_say():
    forms = [f.casefold() for f in spoken_forms("PRJ-005")]

    assert any("green paradise" in f for f in forms), forms


def test_a_date_knows_the_way_people_write_it():
    forms = spoken_forms("2026-08-30")

    assert "30/08" in forms, forms
    assert "30/08/2026" in forms, forms


def test_an_unknown_value_is_left_alone():
    assert spoken_forms("PRJ-KHONG-CO") == ("PRJ-KHONG-CO",)


# --- guard: điều vừa gõ không bị coi là ký ức --------------------------------


def test_the_project_you_just_named_is_not_treated_as_a_memory():
    """Đây là lỗi được báo."""
    goal = "Đặt lịch tham quan Vinhomes Green Paradise ngày 30/08 lúc 10:00"

    offending = Planner._fields_taken_from_recall(_plan(), NHO_LAI, {}, goal)

    assert offending == [], f"hỏi lại thứ khách vừa gõ: {offending}"


def test_the_date_you_just_typed_is_not_treated_as_a_memory():
    """Giữ NGUYÊN ngày cũ, chỉ đổi giờ — ngày vẫn là điều họ vừa gõ."""
    goal = "Đặt lịch tham quan Vinhomes Green Paradise ngày 27/08 lúc 14:00"

    offending = Planner._fields_taken_from_recall(_plan(date="2026-08-27", time="14:00"), NHO_LAI, {}, goal)

    assert offending == [], f"hỏi lại ngày khách vừa gõ: {offending}"


def test_a_value_only_the_memory_knows_is_still_challenged():
    """Guard KHÔNG được nới: giá trị chỉ có trong ký ức vẫn phải xác nhận lại.

    Khách nói "đặt lại như lần trước" — không nêu dự án nào — mà model điền
    PRJ-005 từ ký ức. Đó đúng là thứ guard sinh ra để bắt.
    """
    goal = "Đặt lịch tham quan như lần trước"

    offending = Planner._fields_taken_from_recall(_plan(), NHO_LAI, {}, goal)

    assert "project_id" in offending, offending


@pytest.mark.parametrize("goal", ["Đặt lịch tham quan Vinhomes Ocean Park ngày 30/08", "Đặt lịch tham quan"])
def test_a_different_project_from_the_memory_is_still_challenged(goal):
    """Model điền dự án của lần trước trong khi khách nói dự án khác/không nói."""
    offending = Planner._fields_taken_from_recall(_plan(), NHO_LAI, {}, goal)

    assert "project_id" in offending, offending
