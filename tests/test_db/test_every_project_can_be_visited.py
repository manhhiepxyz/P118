"""Dự án đã chào bán thì phải đặt lịch tham quan được.

`src/common/projects.py` chào BẢY dự án; `tour_slot_config` chỉ có khung giờ
cho hai. Sáu dự án còn lại đi trọn quy trình — nhận yêu cầu, vào hàng đợi, đơn
vị bấm duyệt — rồi mới hỏng ở bước cuối với `TOUR_SLOT_NOT_OFFERED`.

Đo được trên stack thật, "Vinhomes Green Paradise":

    duyệt lịch tham quan → HTTP 502
    T1 schedule_property_viewing FAILED
    T2 register_vehicle          FAILED   ← không liên quan gì
    T3 book_parking              FAILED
    T4 pay_fee                   FAILED

Người dùng mất cả lượt chờ duyệt để nhận một lời từ chối lẽ ra phải nói ngay,
và ba bước không liên quan chết theo.

Test này chặn nguyên nhân gốc: thêm một dự án vào danh mục mà quên khung giờ.
"""

from __future__ import annotations

import pytest

from src.common.projects import PROJECTS


@pytest.mark.asyncio
async def test_every_offered_project_has_tour_slots(db_pool):
    rows = await db_pool.fetch("SELECT residential_area, tour_slot FROM tour_slot_config")
    co_slot = {row["residential_area"] for row in rows}
    thieu = sorted(p["project_name"] for p in PROJECTS if p["project_name"] not in co_slot)
    assert not thieu, (
        f"{thieu} được chào bán nhưng đơn vị tour không có khung giờ nào — "
        "yêu cầu sẽ hỏng SAU khi đã bắt người duyệt bấm nút"
    )


@pytest.mark.asyncio
async def test_both_halves_of_the_day_are_offered(db_pool):
    """Chỉ có MORNING thì mọi lịch buổi chiều đều hỏng, theo cùng một đường."""
    rows = await db_pool.fetch("SELECT residential_area, tour_slot FROM tour_slot_config")
    theo_khu: dict[str, set[str]] = {}
    for row in rows:
        theo_khu.setdefault(row["residential_area"], set()).add(row["tour_slot"])
    for project in PROJECTS:
        slots = theo_khu.get(project["project_name"], set())
        assert {"MORNING", "AFTERNOON"} <= slots, (
            f"{project['project_name']} thiếu khung {sorted({'MORNING', 'AFTERNOON'} - slots)}"
        )
