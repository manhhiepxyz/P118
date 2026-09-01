"""Đường gõ tự do phải theo đúng luật mà biểu mẫu đã áp.

Owner: Thành Bảo (Decision layer)
File: tests/test_the_free_text_path_obeys_the_form_rules.py

Hai lỗi tìm được khi quét toàn bộ dịch vụ trên stack demo.

## Biển số sai vẫn gửi tới đơn vị

    gõ: "…Xe máy biển số ABCXYZ chỗ đỗ Khu A"
    →   WAITING_APPROVAL · 3 bước
    →   plate_number: "ABCXYZ" nằm trong kế hoạch, đã vào hàng đợi đơn vị

Luật biển số có ở HAI nơi — `_extract_plate_number` (trả None cho "ABCXYZ") và
`pattern` của biểu mẫu — nhưng KHÔNG ở `TaskPlanValidator`. Đường nhanh trích
giá trị bằng model chứ không qua bộ đọc, nên không lớp nào chặn.

## Hai lớp ngày, hai kết cục

    ngày quá khứ   → NEEDS_INFORMATION · 0 bước   hỏi lại, khách sửa được
    ngày quá xa    → VALIDATION_ERROR  · 1 bước   ngõ cụt, không hỏi gì

Cùng một loại sai, hai đường ra. Khác biệt không nằm ở Validator — cả hai đều
ném `ValueError` — mà ở PLANNER: nó từ chối ngày quá khứ nhưng vui vẻ lập kế
hoạch cho một ngày năm 2037.

Để kết quả phụ thuộc vào việc model có nhớ luật hay không là để nó đổi theo từng
lượt. Ngày sai là ngày CÓ THỂ HỎI LẠI — cả hai lớp — nên Validator phải phát tín
hiệu hỏi lại, và khi ấy Planner nhớ hay quên đều không đổi kết cục.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.agents.validator import MissingRequiredInputError, TaskPlanValidator
from src.common.schedule_policy import MAX_HORIZON_DAYS
from src.common.task_plan import TaskPlan

SAP_TOI = (date.today() + timedelta(days=7)).isoformat()
HOM_QUA = (date.today() - timedelta(days=1)).isoformat()
QUA_XA = (date.today() + timedelta(days=MAX_HORIZON_DAYS + 30)).isoformat()


def _plan_xe(bien: str) -> TaskPlan:
    return TaskPlan.model_validate(
        {
            "goal": "đăng ký xe",
            "tasks": [
                {
                    "task_id": "T1",
                    "tool": "register_vehicle",
                    "depends_on": [],
                    "input": {"resident_id": "RES-1", "plate_number": bien, "vehicle_type": "motorcycle"},
                }
            ],
        }
    )


def _plan_tham_quan(ngay: str) -> TaskPlan:
    return TaskPlan.model_validate(
        {
            "goal": "đặt lịch tham quan",
            "tasks": [
                {
                    "task_id": "T1",
                    "tool": "schedule_property_viewing",
                    "depends_on": [],
                    "input": {"project_id": "PRJ-004", "viewing_date": ngay, "viewing_time": "09:30"},
                }
            ],
        }
    )


# ĐÂY LÀ BIỂN SỐ ĐÃ ĐI TỚI ĐƠN VỊ.
@pytest.mark.parametrize(
    "bien",
    ["ABCXYZ", "50A-82812312", "A-12345", "5912345", "59A-12", "", "khong biet"],
)
def test_a_plate_the_form_would_reject_never_reaches_a_plan(bien: str) -> None:
    with pytest.raises(ValueError):
        TaskPlanValidator.validate(_plan_xe(bien))


@pytest.mark.parametrize("bien", ["59A-12345", "30A-123.45", "51F 6789", "29AB-1234"])
def test_a_real_plate_still_passes(bien: str) -> None:
    """Hàng rào: luật mới không được chặn biển số thật."""
    TaskPlanValidator.validate(_plan_xe(bien))


# HAI LỚP NGÀY, MỘT KẾT CỤC.
@pytest.mark.parametrize("ngay", [HOM_QUA, QUA_XA])
def test_both_kinds_of_bad_date_are_asked_again(ngay: str) -> None:
    """Ngày sai là ngày HỎI LẠI ĐƯỢC, không phải ngõ cụt."""
    with pytest.raises(MissingRequiredInputError) as loi:
        TaskPlanValidator.validate(_plan_tham_quan(ngay))
    assert "viewing_date" in loi.value.missing_fields


def test_a_date_inside_the_window_still_passes() -> None:
    TaskPlanValidator.validate(_plan_tham_quan(SAP_TOI))


def test_a_malformed_date_is_still_a_hard_error() -> None:
    """Chuỗi không phải ngày thì không phải "chọn ngày khác" — nó là dữ liệu hỏng."""
    with pytest.raises(ValueError) as loi:
        TaskPlanValidator.validate(_plan_tham_quan("hôm nào cũng được"))
    assert not isinstance(loi.value, MissingRequiredInputError)
