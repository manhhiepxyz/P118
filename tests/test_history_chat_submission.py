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


def test_a_follow_up_is_rendered_before_the_network_wait_finishes() -> None:
    handler = PAGE[PAGE.index("async function handleFollowUp") : PAGE.index("async function handleClarification")]
    assert handler.index("setPendingFollowUp(message)") < handler.index("await startWorkflow")
    assert 'data-pending-follow-up="true"' in PAGE


def test_failed_workflow_does_not_render_a_second_chat_composer() -> None:
    # Một composer canonical ở cuối Trao đổi, một composer cho clarification.
    # Nhánh FAILED không được tự dựng thêm ô thứ ba.
    assert PAGE.count("<ClarificationReply") == 2


def test_internal_planning_context_never_replaces_the_public_user_message() -> None:
    routes = (ROOT / "src/api/routes.py").read_text(encoding="utf-8")
    run_job = routes[routes.index("async def _run_demo_job") : routes.index("async def _persist_events")]
    assert 'goal=job.get("goal") or goal' in run_job
