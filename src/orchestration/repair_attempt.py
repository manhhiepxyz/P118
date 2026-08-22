"""Một giá trị mới là một YÊU CẦU MỚI, không phải một lần gửi lại.

Owner: Thành Bảo (Decision layer)
File: src/orchestration/repair_attempt.py

Vấn đề đo được (workflow e9d94655)
----------------------------------
Khách đặt chỗ đỗ xe Khu A, provider trả `NO_AVAILABILITY`, và bằng chứng gửi đi
của bước ấy dừng ở `UNKNOWN` — hệ thống KHÔNG chứng minh được provider đã ghi
nhận hay chưa. Khách trả lời "khu B". `rerun_with_answers` vá `parking_zone` vào
CHÍNH bước cũ rồi chạy lại nó, nên `prepare_submission` từ chối:

    provider_gateway  tu choi gui: ALREADY_TERMINAL
    T5 book_parking   INTERNAL_SERVICE_ERROR
    parking_bookings  không có dòng nào cho Khu B
    T8 pay_fee        PENDING vĩnh viễn

Cổng chặn ở đây ĐÚNG và không được nới. `UNKNOWN` nghĩa là "có thể đã đặt rồi";
gửi lại trên cùng một bằng chứng là chấp nhận đặt chỗ lần hai để đổi lấy việc
màn hình hết kẹt. Reset `UNKNOWN` về `NOT_SUBMITTED` cũng vậy — nó xoá đúng
thứ duy nhất còn ghi lại rằng một lời gọi đã rời khỏi hệ thống.

Cái sai nằm ở TẦNG SỬA LỖI, không ở cổng
----------------------------------------
Khu B không phải lần gửi thứ hai của yêu cầu Khu A. Nó là một yêu cầu khác:

    đơn vị chưa duyệt nó        (họ ký cho Khu A, không phải Khu B)
    provider chưa nhận nó       (chưa lời gọi nào mang Khu B)
    nó chưa có bằng chứng nào   (nên nó KHÔNG kế thừa bằng chứng của Khu A)

Vậy nó phải có DANH TÍNH riêng. Module này cấp cho nó một `task_id` mới, một
dòng `workflow_tasks` mới với `provider_submission_status = NOT_SUBMITTED` và
không khoá idempotency; lần thử Khu A giữ nguyên mọi thứ nó có — input, mã lỗi,
bằng chứng `UNKNOWN` — và được đánh dấu `CANCELLED` nghĩa là ĐÃ BỊ THAY THẾ.

Vì sao `CANCELLED` chứ không xoá: dòng đó là bản ghi kiểm toán duy nhất trả lời
được câu "đã có gì rời khỏi hệ thống chưa". Vì sao `CANCELLED` chứ không giữ
`FAILED`: kế hoạch được dựng lại từ chính các dòng này ở mọi lượt resume, và
một bước `FAILED` trong kế hoạch sẽ được chạy lại — tức đâm lại vào đúng cổng
`ALREADY_TERMINAL`, mãi mãi. `error_code`/`error_message` vẫn nằm nguyên trên
dòng ấy, nên lịch sử "Khu A hết chỗ" không mất đi đâu cả.

Không có Planner ở đây: hình dạng kế hoạch không đổi, chỉ danh tính của một
bước đổi. Gọi model để đổi một `task_id` là mở lại một ván đã thắng.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.common.task_plan import InputRef, Task, TaskPlan

logger = logging.getLogger(__name__)

# Bằng chứng ở trạng thái CUỐI: một lời gọi đã kết thúc, hoặc không chứng minh
# được là chưa xảy ra. Hai giá trị này là lý do duy nhất để mở một lần thử mới
# thay vì chạy lại chỗ cũ.
TERMINAL_SUBMISSION_STATUSES = frozenset({"ACKNOWLEDGED", "UNKNOWN"})

# `workflow_tasks.task_id` là VARCHAR(20). Hậu tố phải ngắn và phải đọc được
# bằng mắt trong một bảng dữ liệu.
_ATTEMPT_SEPARATOR = "R"
_MAX_TASK_ID = 20


@dataclass(frozen=True)
class SupersededTask:
    """Một bước bị thay thế bởi một lần thử mới."""

    old_task_id: str
    new_task_id: str
    tool: str


def _persisted_input(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("input_data") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _needs_new_identity(row: dict[str, Any], task: Task, answered: dict[str, Any], *, refused: bool = False) -> bool:
    """Bước này có phải một YÊU CẦU KHÁC so với thứ đã đi ra ngoài không?

    Hai điều kiện, và cả hai phải cùng đúng:

      giá trị người dùng vừa trả lời KHÁC giá trị đã lưu
          thiếu → mọi bước hỏng đều bị nhân bản, kể cả bước không liên quan; và
                  trả lời đúng y giá trị cũ cũng sinh ra một lần thử mới, khiến
                  đơn vị bị hỏi duyệt lại đúng thứ họ vừa duyệt

      bước này đã RỜI KHỎI hệ thống theo một trong hai nghĩa
          bằng chứng gửi đi ở trạng thái CUỐI  — provider có thể đã ghi nhận
          đơn vị đã TỪ CHỐI nó                 — họ đã ký một quyết định

    Vế thứ hai vốn chỉ có nửa đầu, và nửa thiếu ấy bỏ lọt đúng ca hay gặp nhất.
    Đơn vị từ chối TRƯỚC lúc gửi, nên `provider_submission_status` vẫn là
    `NOT_SUBMITTED`: bản cũ kết luận "chưa gửi đi bao giờ, chạy lại tại chỗ
    được". Hậu quả đo được ở tầng dưới: `save_pending_service_approvals` gặp
    lại đúng khoá `(workflow_id, task_id)` và `ON CONFLICT DO UPDATE` ghi đè
    dòng `REJECTED` — xoá mất mã, lý do và chữ ký của người đã từ chối. Yêu cầu
    Khu B khi ấy thừa hưởng một dòng duyệt mà không ai duyệt cho nó.

    `refused` do người gọi tra từ hàng đợi duyệt trong database, không suy từ
    câu lỗi và không nhận từ client.
    """
    da_ra_ngoai = refused or row.get("provider_submission_status") in TERMINAL_SUBMISSION_STATUSES
    if not da_ra_ngoai:
        return False
    cu = _persisted_input(row)
    for field, gia_tri in answered.items():
        if field in cu and cu[field] != gia_tri:
            return True
    return False


def _allocate_task_id(old_task_id: str, taken: set[str]) -> str | None:
    """`T5` → `T5R2`, `T5R3`… Trả None khi không còn tên nào đủ ngắn.

    Không cắt bớt để cho vừa: một `task_id` bị cắt có thể ĐỤNG một id đang có,
    và khi đó lần thử mới ghi đè lên bằng chứng của một bước khác.
    """
    goc = old_task_id.split(_ATTEMPT_SEPARATOR)[0]
    for lan in range(2, 100):
        ung_vien = f"{goc}{_ATTEMPT_SEPARATOR}{lan}"
        if len(ung_vien) <= _MAX_TASK_ID and ung_vien not in taken:
            return ung_vien
    return None


def _rewrite(plan: TaskPlan, doi_ten: dict[str, str], input_cu: dict[str, dict[str, Any]]) -> TaskPlan:
    """Kế hoạch mới: bước bị thay thế giữ input ĐÃ CHẠY, bước mới nhận input mới.

    Mọi tham chiếu phải đi theo — `depends_on` và cả `InputRef.from_task`. Bỏ
    sót `InputRef` thì `pay_fee` vẫn trỏ vào chỗ đỗ Khu A không bao giờ tồn
    tại, và nó chết với `DEPENDENCY_ERROR` sau khi khách đã trả tiền cho một
    thứ khác.
    """

    def theo(task_id: str) -> str:
        return doi_ten.get(task_id, task_id)

    tasks: list[Task] = []
    for task in plan.tasks:
        moi = theo(task.task_id)
        if task.task_id in doi_ten:
            # Bước CŨ ở lại kế hoạch với input nó thật sự đã chạy — đó là bản
            # ghi lịch sử, không phải dự định.
            tasks.append(
                Task(
                    task_id=task.task_id,
                    tool=task.tool,
                    depends_on=list(task.depends_on),
                    input=dict(input_cu.get(task.task_id, task.input)),
                )
            )
        input_moi: dict[str, Any] = {}
        for khoa, gia_tri in task.input.items():
            if isinstance(gia_tri, InputRef):
                input_moi[khoa] = InputRef(from_task=theo(gia_tri.from_task), field=gia_tri.field)
            else:
                input_moi[khoa] = gia_tri
        tasks.append(
            Task(
                task_id=moi,
                tool=task.tool,
                depends_on=[theo(dep) for dep in task.depends_on],
                input=input_moi,
            )
        )
    return TaskPlan(goal=plan.goal, tasks=tasks)


async def _refused_task_ids(repository: Any, workflow_id: str) -> set[str]:
    """`task_id` mà đơn vị đã từ chối hoặc đã hết hạn chờ.

    Đọc từ database qua repository. Không nhận từ client và không suy từ câu
    lỗi: "đơn vị đã quyết định gì" là một sự kiện có bản ghi, và mọi cách khác
    để trả lời nó đều là đoán.
    """
    try:
        rows = await repository.service_approvals_for(workflow_id)
    except Exception:  # noqa: BLE001 - không có hàng đợi thì coi như chưa ai từ chối
        logger.warning("khong doc duoc hang doi duyet; coi nhu chua co loi tu choi nao")
        return set()
    return {r["task_id"] for r in rows if str(r.get("status")) in {"REJECTED", "EXPIRED"}}


async def open_new_attempts(
    repository: Any, workflow_id: str, plan: TaskPlan, answers: dict[str, Any]
) -> tuple[TaskPlan, list[SupersededTask]]:
    """Cấp danh tính mới cho những bước mà câu trả lời đã biến thành yêu cầu khác.

    Không có bước nào như vậy → trả nguyên kế hoạch, không chạm database. Đây
    là đường đi của phần lớn lượt sửa (đổi ngày trên một bước CHƯA gửi đi), và
    nó không được trả giá cho trường hợp hiếm.

    `plan` phải là kế hoạch ĐÃ vá câu trả lời của người dùng.
    """
    if not answers:
        return plan, []

    rows = {row["task_id"]: row for row in await repository.list_tasks(workflow_id)}
    # Bước nào đã bị đơn vị từ chối — đọc từ chính hàng đợi duyệt, nguồn có
    # thẩm quyền duy nhất cho câu hỏi ấy.
    tu_choi = await _refused_task_ids(repository, workflow_id)
    taken = set(rows)
    doi_ten: dict[str, str] = {}
    input_cu: dict[str, dict[str, Any]] = {}
    thay_the: list[SupersededTask] = []

    for task in plan.tasks:
        row = rows.get(task.task_id)
        if row is None or not _needs_new_identity(row, task, answers, refused=task.task_id in tu_choi):
            continue
        moi = _allocate_task_id(task.task_id, taken)
        if moi is None:
            # Fail-closed: không có tên an toàn thì KHÔNG mở lần thử nào. Đi
            # tiếp ở đây nghĩa là chạy lại trên bằng chứng cũ — đúng thứ đang sửa.
            logger.warning("khong cap duoc danh tinh moi cho mot lan thu sua loi")
            return plan, []
        taken.add(moi)
        doi_ten[task.task_id] = moi
        input_cu[task.task_id] = _persisted_input(row)
        thay_the.append(SupersededTask(old_task_id=task.task_id, new_task_id=moi, tool=task.tool))

    if not doi_ten:
        return plan, []

    plan_moi = _rewrite(plan, doi_ten, input_cu)
    moi_theo_id = {task.task_id: task for task in plan_moi.tasks}
    for item in thay_the:
        task_moi = moi_theo_id[item.new_task_id]
        await repository.supersede_task_with_new_attempt(
            workflow_id,
            old_task_id=item.old_task_id,
            new_task={
                "id": task_moi.task_id,
                "tool": task_moi.tool,
                "depends_on": list(task_moi.depends_on),
                "input": dict(task_moi.input),
            },
        )
        logger.info("mo lan thu moi cho buoc %s -> %s", item.old_task_id, item.new_task_id)

    # Dấu vết hỏng của lần thử cũ đã hết ý nghĩa: câu hỏi của nó vừa được trả
    # lời. Để nguyên thì `_demo_response` vẫn dựng `NEEDS_INFORMATION` và giao
    # diện vẫn hiện ô nhập khu — người dùng trả lời xong mà màn hình không đổi.
    await repository.clear_repair_hints(workflow_id, [item.old_task_id for item in thay_the])
    return plan_moi, thay_the
