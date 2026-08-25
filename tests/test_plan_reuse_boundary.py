"""Vá kế hoạch cũ thay vì hỏi Planner lại — và chỉ khi ĐƯỢC PHÉP.

Đo được trên một lần sửa đúng MỘT ô: 175 giây trong Planner trên tổng 200
giây, hai lượt gọi model. Kế hoạch đã có và đã qua Validator; đem nó ra hỏi
lại là đặt cược lại một ván đã thắng — và ván ấy thua thật: cùng một câu, ba
lượt chạy cho ba kết quả khác nhau (READY / thiếu project_id / không hiểu).

Nhưng ranh giới phải hẹp. Kế hoạch cũ không diễn đạt được ý ĐỔI HÌNH DẠNG
("bỏ chỗ đỗ đi, chỉ giữ tham quan"), nên chỉ dữ liệu có cấu trúc mới đi đường
này; câu chữ tự do vẫn lập lại như cũ.
"""

from __future__ import annotations

import inspect
import re

import pytest

from src.orchestration import demo_service
from src.orchestration.demo_service import RetryNotAllowed


def test_the_fast_path_validates_the_patched_plan() -> None:
    """Vá một giá trị vào plan rồi chạy thẳng là đi vòng qua Validator.

    `Executor` trần KHÔNG validate — việc đó do `ValidatedExecutionBoundary`
    làm, mà đường này không đi qua nó. Bỏ bước validate là mở một cửa sau vào
    tầng thực thi.
    """
    source = inspect.getsource(demo_service.rerun_with_answers)

    assert "TaskPlanValidator.validate(" in source, "kế hoạch đã vá không được validate lại"
    assert "_apply_user_answers(" in source, "không dùng chung hàm áp câu trả lời với graph"


def test_the_fast_path_reuses_the_seeding_helper() -> None:
    """Bước đã SUCCESS không được chạy lại — các tool này không idempotent.

    Một bản sao thứ hai của phần seed là chỗ để hai đường lệch nhau, và thứ
    lệch được ở đây là "có đặt lại một chỗ đỗ đã tính phí hay không".
    """
    source = inspect.getsource(demo_service.rerun_with_answers)

    assert "_seed_completed(" in source
    assert "on_failure=repair_manager" in source, "hỏng lần nữa thì không sinh được câu hỏi lại"


def test_the_route_requires_a_parsed_answer_and_a_prior_plan() -> None:
    """Hai điều kiện, thiếu một là đi đường cũ.

    `answers`   — câu ĐÃ ĐƯỢC PHÂN TÍCH thành giá trị canonical cho đúng những
                  ô đang hỏi. Không map được ô nào thì không có gì để vá.
    repair hint — không có nghĩa là chưa từng có kế hoạch chạy hỏng, tức đây là
                  lần hỏi ĐẦU và không có gì để tái dùng.

    `request.fields` KHÔNG còn là điều kiện. Nó từng có, với lý lẽ "câu chữ tự
    do có thể mang ý đổi hình dạng kế hoạch" — đúng với CHUỖI THÔ, nhưng thứ đi
    tiếp là `answers`, đã qua `_extract_follow_up_answers`: chỉ rút giá trị cho
    đúng những ô đang hỏi, dạng canonical, không thêm hay bớt dịch vụ nào.

    Đo được khi còn đòi `fields`: khách gõ "đổi qua ngày 25" thì cả ba yêu cầu
    được gửi lại, và `book_parking` — đã SUCCESS từ trước — hỏng với
    `BOOKING_ALREADY_EXISTS` cho một ngày khách không nhắc tới.
    """
    from src.api import routes

    source = inspect.getsource(routes.continue_demo_workflow)

    assert "if answers and await _read_repair_hints(workflow_id):" in source, (
        "điều kiện vào đường nhanh đã đổi — kiểm lại xem nó còn đúng không"
    )
    assert "rerun_with_answers(" in source


def test_a_failure_on_the_fast_path_falls_back_instead_of_erroring() -> None:
    """Không vá được thì lập lại như cũ, KHÔNG báo lỗi.

    Người dùng vừa trả lời đúng; họ không có lỗi gì để nghe. Ném lỗi ở đây là
    biến một tối ưu nội bộ thành một thất bại nhìn thấy được.
    """
    from src.api import routes

    source = inspect.getsource(routes.continue_demo_workflow)
    assert "except RetryNotAllowed as exc:" in source
    assert "lập lại từ đầu" in source


def test_the_fast_path_closes_the_open_question() -> None:
    """Chạy tiếp trên CHÍNH workflow đó nên không có child để claim.

    Để ngỏ câu hỏi thì workflow nằm mãi ở "chờ bổ sung": chiếm một suất hạn
    ngạch ngày và là một dòng đang-chờ trong Lịch sử vĩnh viễn.
    """
    from src.api import routes

    source = inspect.getsource(routes.continue_demo_workflow)
    assert "resolve_clarification(" in source


@pytest.mark.asyncio
async def test_a_missing_workflow_is_refused_not_crashed(monkeypatch) -> None:
    class _Pool:
        async def close(self):
            return None

    class _Repo:
        _pool = _Pool()

        async def get_workflow(self, _workflow_id):
            return None

    async def _acquire():
        return _Repo()

    monkeypatch.setattr(demo_service, "acquire_repository", _acquire)

    with pytest.raises(RetryNotAllowed) as caught:
        await demo_service.rerun_with_answers("wf-1", {"parking_zone": "ZONE_B"})

    assert caught.value.code == "NOT_FOUND"


# --- Cổng tiền: đường tắt KHÔNG được đi qua ---------------------------------


def _plan_with(tools: list[str]):
    from src.common.task_plan import Task, TaskPlan

    return TaskPlan(
        goal="đăng ký xe và đặt chỗ đỗ",
        tasks=[Task(task_id=f"T{i + 1}", tool=tool, input={}, depends_on=[]) for i, tool in enumerate(tools)],
    )


def test_the_shortcut_runs_behind_the_payment_gate() -> None:
    """Đường tắt KHÔNG còn từ chối `pay_fee` — nó chạy sau cổng duyệt.

    Bản trước chặn hẳn, vì `Executor` trần là đường vòng quanh mọi boundary và
    nó đã trừ 100.000 VND với 0 bản ghi duyệt. Chặn là đúng lúc đó, nhưng cái
    giá là luồng đỗ xe — luôn có `pay_fee` — không bao giờ được hưởng tốc độ.

    Giờ chuỗi boundary nhận `seed_statuses`/`seed_results`, nên bọc được mà vẫn
    giữ phần seed. `pay_fee` dừng đúng chỗ nó phải dừng, và đường tắt áp dụng
    cho cả luồng có phí.
    """
    for name in ("rerun_with_answers", "retry_failed_tasks"):
        source = inspect.getsource(getattr(demo_service, name))
        assert "PaymentApprovalBoundary(" in source, f"{name} chạy Executor trần, không qua cổng tiền"
        assert "ValidatedExecutionBoundary(" in source, f"{name} không validate plan trước khi chạy"
        assert "False,  # KHÔNG bao giờ pre-approve" in source, f"{name} có thể tự duyệt thanh toán"


def test_the_shortcut_treats_an_approval_pause_as_waiting_not_failure() -> None:
    """Dừng lại hỏi KHÔNG phải lỗi.

    Boundary ném `PolicyInterruptionError` khi cần duyệt tiền. Không bắt thì
    đường tắt đánh workflow FAILED trong khi nó chỉ đang chờ người dùng bấm —
    và bản ghi duyệt đã được ghim, nên thẻ xác nhận vẫn hiện: hai tầng nói hai
    chuyện khác nhau về cùng một workflow.
    """
    for name in ("rerun_with_answers", "retry_failed_tasks"):
        source = inspect.getsource(getattr(demo_service, name))
        assert "except PolicyInterruptionError as pause:" in source, f"{name} coi việc dừng lại hỏi là lỗi"
        # Bắt lỗi thôi CHƯA đủ: việc GHIM yêu cầu duyệt là của caller, và
        # đường chạy thường làm điều đó ở `_run_demo_job`. Bỏ bước ấy thì đo
        # được `pay_fee` PENDING, `payment_approvals` 0 dòng, workflow
        # WAITING_APPROVAL — không rò tiền, nhưng người dùng chờ một nút không
        # tồn tại.
        assert "persist_pending_approval(" in source, f"{name} không ghim yêu cầu duyệt — người dùng kẹt"
        assert "WorkflowStatus.WAITING_APPROVAL" in source


@pytest.mark.parametrize("func", ["rerun_with_answers", "retry_failed_tasks"])
def test_no_shortcut_talks_to_the_executor_directly(func: str) -> None:
    """`Executor` trần là đường vòng quanh MỌI boundary.

    Đây là bài học đắt nhất của phiên này: mỗi đường tắt thêm vào vì một lý do
    chính đáng — resume, retry, vá plan — là thêm một cửa sau. Guard chặn
    `pay_fee` từng là câu trả lời, nhưng nó chỉ chặn được đúng một tool.

    Luật giờ đơn giản hơn và rộng hơn: không ai được gọi `Executor` mà không
    bọc. Bọc rồi thì `pay_fee` dừng đúng chỗ, và những guard ad-hoc kia không
    cần tồn tại.
    """
    source = inspect.getsource(getattr(demo_service, func))
    assert "Executor(" in source, "cập nhật lại test: đường tắt không còn dựng Executor"
    # So khớp sau khi BỎ khoảng trắng: bám vào cách xuống dòng nghĩa là một lần
    # định dạng lại cũng làm test đỏ, và cái đỏ ấy không nói được gì về an toàn.
    compact = re.sub(r"\s+", "", source)
    assert "ValidatedExecutionBoundary(Executor(" in compact, (
        f"{func} gọi Executor TRẦN — đường vòng quanh mọi boundary"
    )


def test_the_viewing_resume_never_runs_an_unapproved_fee() -> None:
    """Đường resume sau duyệt lịch cũng dùng `Executor` trần.

    Plan dựng lại từ `workflow_tasks` giữ MỌI task, kể cả `pay_fee`, nên về mặt
    code nó thừa sức gọi Payment API không qua cổng duyệt.

    Đo trên dữ liệu thật thì chưa từng xảy ra — mọi workflow đã trả tiền đều có
    bản ghi duyệt, và yêu cầu duyệt luôn được tạo TRƯỚC khi lịch được duyệt
    (5/5). Nhưng "chưa từng xảy ra" không phải một bảo đảm, và cơ chế giữ cho
    nó không xảy ra thì không nằm ở đâu cả.
    """
    source = inspect.getsource(demo_service._materialize_and_run_remaining)

    assert 'task.tool == "pay_fee"' in source, "không tách bước thanh toán khỏi plan chạy ở đây"
    assert "plan_without(" in source, "không dùng bộ tách plan dùng chung"
