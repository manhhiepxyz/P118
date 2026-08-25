"""Danh sách khung giờ ở giao diện phải nằm TRỌN trong biên backend.

Ô giờ vừa đổi từ text sang select. Danh sách chọn nằm ở frontend
(`serviceForms.ts`), còn biên hợp lệ nằm ở `TaskPlanValidator.TIME_INPUTS` —
hai nơi, nên sớm muộn lệch nhau.

Lệch theo hướng nào cũng tệ, nhưng khác nhau:

  * Giao diện đề nghị một giờ backend từ chối → người dùng chọn từ danh sách
    hệ thống đưa ra và vẫn bị báo sai. Không có cách nào để họ đúng.
  * Giao diện thiếu giờ backend cho phép → mất lựa chọn, phiền nhưng không bế
    tắc.

Test này chặn hướng thứ nhất.
"""

from __future__ import annotations

import re
from datetime import time
from pathlib import Path

import pytest

from src.agents.validator import TaskPlanValidator

_FORMS = Path(__file__).resolve().parents[1] / "frontend" / "src" / "lib" / "serviceForms.ts"


def _windows() -> dict[str, tuple[time, time]]:
    return {field: (opens, closes) for field, opens, closes in TaskPlanValidator.TIME_INPUTS.values()}


def _slot_bounds() -> dict[str, tuple[int, int]]:
    """`viewing_time: {... options: slots(8, 17) }` → {'viewing_time': (8, 17)}."""
    source = _FORMS.read_text(encoding="utf-8")
    found: dict[str, tuple[int, int]] = {}
    for match in re.finditer(r"(\w*time\w*):\s*\{[^}]*?slots\((\d+),\s*(\d+)\)", source, re.S):
        found[match.group(1)] = (int(match.group(2)), int(match.group(3)))
    return found


def test_the_form_declares_slots_for_time_fields() -> None:
    """Lá chắn cho phép đọc ở trên: đọc trượt thì mọi test dưới xanh rỗng."""
    assert len(_slot_bounds()) >= 3, "không đọc được khung giờ nào từ serviceForms.ts"


@pytest.mark.parametrize("field", sorted(_windows()))
def test_every_time_field_backend_validates_has_options(field: str) -> None:
    """Ô giờ nào backend kiểm biên thì giao diện phải cho CHỌN, không cho gõ."""
    assert field in _slot_bounds(), f"{field} vẫn là ô text — người dùng gõ được giờ ngoài khung"


@pytest.mark.parametrize(("field", "bounds"), sorted(_slot_bounds().items()))
def test_no_option_falls_outside_the_backend_window(field: str, bounds: tuple[int, int]) -> None:
    """`slots(a, b)` sinh mốc 30 phút từ `a:00` tới `a:00`… `b:00`.

    Mốc CUỐI là `b:00`, KHÔNG phải `b:30`: vòng lặp `break` trước khi thêm mốc
    nửa tiếng của giờ cuối. Bản đầu của test này giả định `b:30` và báo đỏ ba
    ca — phép đo sai, không phải code sai. Đọc lại bộ sinh mới biết.
    """
    window = _windows().get(field)
    if window is None:
        pytest.skip(f"backend không kiểm biên cho {field}")

    first, last = time(bounds[0], 0), time(bounds[1], 0)
    opens, closes = window
    assert first >= opens, f"{field}: giao diện mời {first}, backend mở từ {opens}"
    assert last <= closes, f"{field}: giao diện mời {last}, backend đóng lúc {closes}"
