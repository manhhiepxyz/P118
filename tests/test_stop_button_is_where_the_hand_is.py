"""Nút dừng phải nằm ngay cạnh ô nhập, và câu huỷ chỉ được nói MỘT lần.

Nút dừng vốn nằm trên đầu trang. Người dùng gõ ở đáy, gửi ở đáy, rồi phải đi
tìm một nút khác ở một chỗ khác để dừng — trong lúc việc đang chạy và họ vừa
nhận ra mình gõ nhầm.

Câu huỷ thì có HAI nguồn: câu frontend nói ngay khi bấm, và câu chốt backend
viết cho trạng thái `CANCELLED`. `sayOnce` dedupe theo NỘI DUNG, mà hai câu
khác chữ — nên một lần bấm ra hai câu, và câu thứ hai còn đọng lại sau khi
người dùng đã gõ tiếp, đọc như thể việc MỚI vừa bị huỷ.
"""

from __future__ import annotations

import re
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"
_RAIL = _FRONTEND / "components" / "workspace" / "CommandRail.tsx"
_WORKSPACE = _FRONTEND / "pages" / "JourneyWorkspacePage.tsx"
_REPLY = _FRONTEND / "components" / "ClarificationReply.tsx"


def test_the_composer_button_can_stop_the_work() -> None:
    rail = _RAIL.read_text(encoding="utf-8")
    assert "onStop" in rail, "thanh lệnh không có đường dừng — nút dừng vẫn ở tận đầu trang"
    assert 'aria-label="Dừng việc đang chạy"' in rail, (
        "nút dừng không có tên đọc được; ở chế độ hành trình nút chỉ còn biểu "
        "tượng nên trình đọc màn hình sẽ chỉ nghe thấy 'button'"
    )
    assert "animate-spin" in rail, "thiếu vòng xoay — nút không nói được rằng có việc đang chạy"


def test_the_spinner_is_a_circle_and_the_square_stays_still() -> None:
    """Quay VÒNG TRÒN bên trong, không quay khung vuông của nút.

    Chuyển động xoay chỉ trông đứng yên tại chỗ khi hình dạng tròn. Quay một
    khung bo góc thì chuyển động bám theo bốn góc và mắt đọc ra một cái khung
    đang rung, không phải một thứ đang chạy.

    Và ô vuông KHÔNG quay: nó là cái nút bấm để dừng, không phải một phần của
    chỉ báo tiến trình.
    """
    rail = _RAIL.read_text(encoding="utf-8")
    spinner = rail[rail.index('aria-label="Dừng việc đang chạy"') :]
    spinner = spinner[: spinner.index("</button>")]

    assert "animate-spin rounded-full" in spinner, (
        "vòng xoay không phải hình tròn — quay khung bo góc trông như rung"
    )
    assert "animate-spin rounded-[var(--r-sm)]" not in spinner, "vẫn đang quay chính khung vuông của nút"

    square = spinner[spinner.index("<Square") :]
    assert "animate-spin" not in square[: square.index("/>")], "ô vuông dừng cũng bị cho quay theo"


def test_the_workspace_wires_its_stop_function_into_the_composer() -> None:
    """Có nút mà không nối thì nút không làm gì."""
    page = _WORKSPACE.read_text(encoding="utf-8")
    assert re.search(r"onStop=\{stopWorkflow\}", page), "thanh lệnh không được nối với stopWorkflow"
    assert re.search(r"stopping=\{stopping\}", page), "không khoá nút khi lệnh dừng đang bay"


def test_the_detail_page_chat_box_can_stop_too() -> None:
    reply = _REPLY.read_text(encoding="utf-8")
    assert "onStop" in reply and "busy" in reply, "khung chat trang chi tiết không dừng được"
    page = (_FRONTEND / "pages" / "WorkflowPage.tsx").read_text(encoding="utf-8")
    assert page.count("onStop={handleCancel}") == page.count("<ClarificationReply"), (
        "có khung chat chưa được nối nút dừng — lỗi sẽ chỉ xuất hiện ở một nhánh"
    )


def test_a_cancellation_is_announced_once_per_request() -> None:
    """Chặn theo ID yêu cầu, không theo một cờ bật/tắt.

    Cờ phải mở lại ở đâu đó, và mọi thời điểm mở đều sai: mở lúc gửi lượt mới
    thì nhịp poll CUỐI của yêu cầu vừa huỷ vẫn đang bay, nó về sau khi cờ đã
    mở, và câu huỷ lọt ra giữa lúc việc mới đang chạy. Đo được: dừng xong 1
    câu, gõ tiếp một câu nữa là thành 2.
    """
    page = _WORKSPACE.read_text(encoding="utf-8")
    assert "stopAnnouncedFor" in page, "không có cơ chế chặn câu huỷ lặp"
    assert "res.workflow_id === stopAnnouncedFor.current" in page, (
        "chặn theo cờ chung thay vì theo đúng yêu cầu đã huỷ"
    )
    assert "stopAnnounced.current = false" not in page, (
        "vẫn còn chỗ mở lại cờ — đó chính là kẽ hở khiến câu huỷ thứ hai lọt ra"
    )
