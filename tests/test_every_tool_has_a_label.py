"""Mọi tool được phép đều phải có nhãn tiếng Việt ở giao diện.

`toolLabel` trả về NGUYÊN TÊN TOOL khi thiếu, nên người dùng đọc được
"book_shuttle" giữa một hàng nhãn tiếng Việt — đo được trên màn hình thật.

Bảng nhãn nằm ở frontend còn danh sách tool nằm ở backend, nên không có gì
buộc chúng khớp nhau ngoài test này.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _allowed_tools() -> set[str]:
    source = (_ROOT / "src" / "common" / "task_plan.py").read_text(encoding="utf-8")
    block = source[source.index("AllowedTool = Literal[") : source.index("]", source.index("AllowedTool = Literal["))]
    return set(re.findall(r'"([a-z_]+)"', block))


def _labelled_tools() -> set[str]:
    source = (_ROOT / "frontend" / "src" / "lib" / "status.ts").read_text(encoding="utf-8")
    start = source.index("export const TOOL_LABELS")
    block = source[start : source.index("\n}", start)]
    return set(re.findall(r"^  ([a-z_]+):", block, re.M))


def test_the_tool_list_is_not_empty() -> None:
    """Lá chắn cho chính hai hàm đọc ở trên: đọc trượt thì test dưới xanh rỗng."""
    assert len(_allowed_tools()) >= 8
    assert len(_labelled_tools()) >= 8


def test_every_allowed_tool_has_a_vietnamese_label() -> None:
    missing = sorted(_allowed_tools() - _labelled_tools())
    assert not missing, f"tool không có nhãn, giao diện sẽ hiện tên thô: {missing}"


def test_no_label_points_at_a_tool_that_does_not_exist() -> None:
    """Nhãn thừa là dấu hiệu tool đã đổi tên mà giao diện chưa theo."""
    extra = sorted(_labelled_tools() - _allowed_tools())
    assert not extra, f"nhãn trỏ tới tool không còn tồn tại: {extra}"
