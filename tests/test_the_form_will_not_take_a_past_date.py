"""Ô ngày phải KHOÁ quá khứ, đừng để người dùng gõ rồi chờ lời từ chối.

Owner: Thành Bảo (Decision layer)
File: tests/test_the_form_will_not_take_a_past_date.py

`TaskPlanValidator` từ chối ngày quá khứ — đúng, nhưng nó từ chối SAU khi người
dùng đã gõ xong và đã chờ. Đo được trên stack demo, workflow aa53d5aa: yêu cầu
mang `move_date` là ngày hôm qua, và người dùng trả giá bằng `plan 65,49s` cho
một lời từ chối mà trình duyệt có thể nói ngay lúc bấm.

Ô `<input type="date">` nhận `min`/`max` sẵn có: đặt chúng thì lịch chọn tự mờ
những ngày không hợp lệ. Không có gì thông minh ở đây — chỉ là nói ra sớm điều
backend sẽ nói muộn.

`PendingCard` đã làm đúng (`min={field.minDate}`). Hai chỗ còn lại thì không, và
đó chính là hai chỗ người dùng gõ yêu cầu MỚI — nơi lỗi tốn nhiều thời gian nhất.

Chặn trên cũng đặt: `MAX_HORIZON_DAYS` là luật của backend, và một ngày năm 2099
cũng bị từ chối y như ngày hôm qua.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.common.schedule_policy import MAX_HORIZON_DAYS

_GOC = Path(__file__).resolve().parents[1] / "frontend" / "src"
_O_NGAY = [
    _GOC / "components" / "workspace" / "InlineServiceForm.tsx",
    _GOC / "components" / "workspace" / "PendingCard.tsx",
    _GOC / "components" / "QuickActionForm.tsx",
]


@pytest.mark.parametrize("duong", _O_NGAY, ids=lambda p: p.name)
def test_every_date_input_blocks_the_past(duong: Path) -> None:
    """Mọi ô ngày đều phải có chặn dưới — không sót chỗ nào."""
    source = duong.read_text(encoding="utf-8")
    assert 'type="date"' in source or "'date'" in source, f"{duong.name} không còn ô ngày?"
    # Kiểm việc DÙNG chặn dùng chung, không kiểm cách viết thuộc tính.
    #
    # `QuickActionForm` truyền qua spread (`{...gioiHan}`), nên soi chuỗi `min=`
    # sẽ báo đỏ cho một bản sửa hoàn toàn đúng — bài kiểm khi ấy canh cú pháp
    # thay vì canh hành vi.
    assert "minDate(" in source, (
        f"{duong.name} có ô ngày mà không khoá quá khứ — người dùng gõ được một ngày "
        f"backend chắc chắn từ chối, và chờ cả lượt lập kế hoạch để biết"
    )


@pytest.mark.parametrize("duong", _O_NGAY, ids=lambda p: p.name)
def test_every_date_input_blocks_the_far_future(duong: Path) -> None:
    """Chặn trên cũng vậy: `MAX_HORIZON_DAYS` là luật backend, không phải gợi ý."""
    source = duong.read_text(encoding="utf-8")
    assert "maxDate(" in source, f"{duong.name} không khoá ngày quá xa"


def test_the_horizon_comes_from_the_backend_rule() -> None:
    """Một luật, hai nơi — kiểm chúng không trôi khỏi nhau.

    Viết cứng số ngày ở frontend thì đổi `MAX_HORIZON_DAYS` sẽ để lại một biểu
    mẫu cho gõ những ngày backend vừa thôi nhận.
    """
    source = (_GOC / "lib" / "dateBounds.ts").read_text(encoding="utf-8")
    assert str(MAX_HORIZON_DAYS) in source, f"frontend không dùng đúng chân trời {MAX_HORIZON_DAYS} ngày của backend"
