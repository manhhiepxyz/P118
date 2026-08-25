"""Mọi màn hình theo dõi workflow phải chờ tới khi CÂU TRẢ LỜI xong.

Backend công bố KẾT QUẢ trước rồi mới sinh câu trả lời ở tác vụ nền — cố ý, để
không cộng một lượt gọi mô hình vào thời gian người dùng phải chờ. Cái giá là
mọi màn hình theo dõi đều phải biết điều đó.

Với một lượt chat, `status` về `CHAT` gần như tức thì, mà `CHAT` nằm trong
`TERMINAL_STATUSES`: trang ngừng hỏi lại NGAY, đúng vào khoảnh khắc câu trả lời
còn chưa được viết. Người dùng gửi xong, thấy hội thoại chỉ có lời của chính
mình, và phải thoát ra vào lại mới đọc được câu đáp — nó vẫn nằm trong
database, chỉ là không ai đi lấy.

Đo được: trước bản vá, câu đáp không xuất hiện trong 50 giây; sau bản vá là 8
giây, không tải lại trang.

Thẻ workflow trong màn hội thoại đã bật cờ này từ lâu; trang chi tiết thì
không. Hai nơi dùng CÙNG một hook, nên chỉ một nơi hỏng — đúng loại lệch mà
không lời gọi nào tự tố cáo.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"

# Mọi nơi hiển thị câu trả lời của P-118 cho một workflow.
_WATCHERS = [
    _FRONTEND / "pages" / "WorkflowPage.tsx",
    _FRONTEND / "components" / "ChatWorkflowCard.tsx",
]


@pytest.mark.parametrize("path", _WATCHERS, ids=lambda p: p.name)
def test_every_watcher_waits_for_the_answer(path: Path) -> None:
    assert path.exists(), f"{path.name} đã đổi chỗ — cập nhật lại danh sách này"
    source = path.read_text(encoding="utf-8")
    assert "useWorkflowPolling(" in source, f"{path.name} không còn theo dõi workflow"
    assert re.search(r"waitForAnswer:\s*true", source), (
        f"{path.name} dừng poll ngay khi workflow kết thúc. Câu trả lời sinh SAU "
        "thời điểm đó, nên nó sẽ không bao giờ tới màn hình cho tới khi người "
        "dùng tải lại trang."
    )


def test_chat_is_still_treated_as_an_end_state() -> None:
    """`waitForAnswer` là phần MỞ RỘNG, không thay thế điều kiện dừng.

    Bỏ `CHAT` khỏi danh sách kết thúc cũng làm câu trả lời hiện ra — bằng cách
    poll mãi mãi. Trần thời gian trong hook mới là thứ giữ vòng lặp có điểm
    dừng, và nó chỉ chạy khi trạng thái ĐƯỢC coi là đã kết thúc.
    """
    hook = (_FRONTEND / "lib" / "useWorkflowPolling.ts").read_text(encoding="utf-8")
    terminal = hook[hook.index("TERMINAL_STATUSES") : hook.index("WAITING_STATUSES")]
    assert "'CHAT'" in terminal, "CHAT không còn là trạng thái kết thúc — trang sẽ poll vô hạn"
    assert "ANSWER_TIMEOUT_MS" in hook, "mất trần thời gian: chờ câu trả lời thành vòng lặp vô hạn"
