"""Regression cho composer khi nói tiếp từ trang Lịch sử.

Các test này khoá boundary UI gây ra lỗi nhìn thấy trên browser: vừa bấm Gửi
thì nút thành Dừng, textarea giữ nguyên và lời người dùng chưa xuất hiện trong
hội thoại suốt thời gian chờ Planner.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPLY = (ROOT / "frontend/src/components/ClarificationReply.tsx").read_text(encoding="utf-8")
PAGE = (ROOT / "frontend/src/pages/WorkflowPage.tsx").read_text(encoding="utf-8")


def test_sending_a_message_is_not_presented_as_stopping_a_workflow() -> None:
    assert "const canStop = Boolean(onStop) && busy" in REPLY
    assert "busy || submitting" not in REPLY
    assert "submitting ? 'Đang gửi…' : 'Gửi'" in REPLY


def test_the_composer_clears_immediately_and_restores_only_on_failure() -> None:
    before_await, after_await = REPLY.split("await onSubmit(text)", maxsplit=1)
    assert "setMessage('')" in before_await
    assert "setMessage(text)" in after_await


# `test_a_follow_up_is_rendered_before_the_network_wait_finishes` và
# `test_failed_workflow_does_not_render_a_second_chat_composer` đã được gỡ cùng
# với thứ chúng kiểm: khung hội thoại + ô nhập tự do ở trang chi tiết. Cả hai
# bám vào `handleFollowUp`, một hàm không còn tồn tại.
#
# Ba test còn lại ở đây kiểm `ClarificationReply` — component ấy VẪN sống, giờ
# chỉ còn một chỗ dùng: trả lời câu hỏi sửa lỗi đang treo. Lý do gỡ và lịch sử
# của ô đã mất nằm ở `tests/test_history_does_not_invite_a_chat.py`.


def test_internal_planning_context_never_replaces_the_public_user_message() -> None:
    routes = (ROOT / "src/api/routes.py").read_text(encoding="utf-8")
    run_job = routes[routes.index("async def _run_demo_job") : routes.index("async def _persist_events")]
    assert 'goal=job.get("goal") or goal' in run_job
