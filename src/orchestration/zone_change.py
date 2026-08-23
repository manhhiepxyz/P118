"""Đổi khu trên một chỗ ĐÃ GIỮ là một BƯỚC MỚI, không phải một lần đặt lại.

Owner: Thành Bảo (Decision layer)
File: src/orchestration/zone_change.py

Vấn đề đo được (workflow 148c9f30)
----------------------------------
    T3   book_parking CANCELLED ZONE_A sub=ACKNOWLEDGED  result BOOK-019
    T3R2 book_parking FAILED    ZONE_B  BOOKING_ALREADY_EXISTS

Khách đã có chỗ Khu A. Họ gõ "tôi muốn đổi qua khu B", `repair_attempt` mở một
lần thử `book_parking` MỚI, và provider từ chối vì `uq_bookings_vehicle_date`
— chính chiếc xe ấy đã có chỗ ngày hôm đó.

`repair_attempt._needs_new_identity` đã học nửa đầu: một bước ĐÃ SUCCESS thì
không thay thế. Nhưng "không thay thế" đứng một mình là một ngõ cụt khác —
khách xin đổi khu và không có gì xảy ra cả. Nửa sau nằm ở đây.

Vì sao là một BƯỚC, không phải một lần thử mới
----------------------------------------------
Lần thử mới nghĩa là "gửi lại yêu cầu cũ với dữ liệu khác", và yêu cầu cũ là
"hãy giữ cho tôi một chỗ". Gửi lại nó khi đã có chỗ là xin chỗ THỨ HAI.

Đổi khu là một yêu cầu khác hẳn: "chỗ tôi đang giữ, chuyển sang khu kia". Nó có
tool riêng (`change_parking_zone`), làm trọn trong một transaction ở phía
provider, và giữ nguyên `booking_id` — nên thẻ chờ thanh toán, hoá đơn và mọi
tham chiếu của khách vẫn trỏ đúng chỗ. Xem `change_booking_zone` để biết vì
sao KHÔNG dùng huỷ-rồi-đặt.

Ba ràng buộc, và mỗi cái bịt một cách hỏng khác nhau
---------------------------------------------------
`booking_id` đọc từ `result_data` của bước ĐÃ CHẠY, không bao giờ từ câu người
dùng: đó cũng là lý do `change_parking_zone` nằm trong `AGENT_FORBIDDEN_TOOLS`
— cho Planner lập kế hoạch với nó là cho model tự viết ra một `booking_id`.

Khu ĐANG GIỮ đọc từ `parking_bookings`, không từ kế hoạch: sau lần đổi thứ
nhất, `book_parking` trong kế hoạch vẫn ghi `ZONE_A` vĩnh viễn (nó là bản ghi
lịch sử). So với nó thì lần đổi thứ hai luôn "hợp lệ", kể cả khi khách xin đúng
khu họ vừa chuyển tới.

Bước mới KHÔNG được nối vào `pay_fee` bằng `depends_on`. Nghe thì hợp lý —
"đừng thu tiền trước khi chốt khu" — nhưng `plan_without` cắt cả nhánh phụ
thuộc khi một bước bị từ chối. Nối vào nghĩa là đơn vị từ chối đổi khu thì
`pay_fee` biến mất, và khách không trả được tiền cho chỗ Khu A vẫn còn nguyên
của mình. Thứ tự đã được `PaymentApprovalBoundary` bảo đảm sẵn: nó tách
`pay_fee` ra chạy sau cùng, sau mọi bước khác trong kế hoạch.
"""

from __future__ import annotations

import logging
from typing import Any

from src.common.enums import TaskStatus
from src.common.task_plan import Task, TaskPlan
from src.db.parking_payment_repository import get_booking
from src.orchestration.payment_approval import (
    payment_task_id,
    quote_from_persisted_book_parking,
    save_pending_approval,
)

logger = logging.getLogger(__name__)

_ZONE_FIELD = "parking_zone"
_CHANGE_TOOL = "change_parking_zone"
_BOOKING_TOOL = "book_parking"

# `workflow_tasks.task_id` là VARCHAR(20). `T5` → `T5Z2`, `T5Z3`… — đọc được
# bằng mắt trong một bảng dữ liệu, và không đụng hậu tố `R` của lần-thử-mới.
_CHANGE_SEPARATOR = "Z"
_MAX_TASK_ID = 20


def _persisted_input(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("input_data") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _allocate_task_id(source_task_id: str, taken: set[str]) -> str | None:
    """Trả None khi không còn tên nào đủ ngắn — và khi đó KHÔNG đổi gì cả.

    Không cắt bớt để cho vừa: một `task_id` bị cắt có thể ĐỤNG một id đang có,
    và khi đó bước đổi khu ghi đè lên bằng chứng của một bước khác.
    """
    goc = source_task_id.split(_CHANGE_SEPARATOR)[0]
    for lan in range(2, 100):
        ung_vien = f"{goc}{_CHANGE_SEPARATOR}{lan}"
        if len(ung_vien) <= _MAX_TASK_ID and ung_vien not in taken:
            return ung_vien
    return None


def _booked_source(plan: TaskPlan, rows: dict[str, dict[str, Any]]) -> tuple[str, str] | None:
    """`(task_id, booking_id)` của chỗ đỗ đã giữ được THẬT, hoặc None.

    Chỉ nhận bước `book_parking` đã `SUCCESS` và có `booking_id` trong
    `result_data`. Một bước FAILED/CANCELLED không tạo ra chỗ nào để đổi, và
    đường sửa lỗi cũ (`repair_attempt`) mới là nơi xử lý nó.
    """
    for task in plan.tasks:
        if task.tool != _BOOKING_TOOL:
            continue
        row = rows.get(task.task_id)
        if row is None or str(row.get("status")) != TaskStatus.SUCCESS.value:
            continue
        result = row.get("result_data")
        booking_id = (result or {}).get("booking_id") if isinstance(result, dict) else None
        if booking_id:
            return task.task_id, str(booking_id)
    return None


async def open_zone_change(
    repository: Any, workflow_id: str, plan: TaskPlan, answers: dict[str, Any]
) -> tuple[TaskPlan, str | None]:
    """Dựng bước đổi khu khi khách trả lời một khu KHÁC khu đang giữ.

    Trả `(plan, None)` và không chạm database ở mọi trường hợp còn lại — đó là
    đường đi của phần lớn lượt sửa, và nó không được trả giá cho ca này.

    `plan` phải là kế hoạch ĐÃ vá câu trả lời của người dùng; hàm này hoàn lại
    ô `parking_zone` của bước đã chạy về đúng giá trị nó thật sự đã chạy.
    """
    khu_moi = answers.get(_ZONE_FIELD) if answers else None
    if not khu_moi:
        return plan, None

    rows = {row["task_id"]: row for row in await repository.list_tasks(workflow_id)}
    nguon = _booked_source(plan, rows)
    if nguon is None:
        return plan, None
    source_task_id, booking_id = nguon

    # Khu ĐANG GIỮ là thứ trong `parking_bookings`, không phải thứ trong kế
    # hoạch. Không đọc được booking thì KHÔNG đoán: im lặng để đường sửa lỗi
    # cũ xử lý còn hơn dựng một yêu cầu đổi khu trên một chỗ không chứng minh
    # được là có thật.
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        booking = await get_booking(pool, booking_id)
    except Exception:  # noqa: BLE001 - chỉ giữ TÊN loại lỗi
        logger.warning("khong doc duoc cho do de doi khu")
        return plan, None
    if booking is None or booking.parking_zone == khu_moi:
        return plan, None

    moi = _allocate_task_id(source_task_id, set(rows))
    if moi is None:
        # Fail-closed: không có tên an toàn thì KHÔNG dựng bước nào.
        logger.warning("khong cap duoc danh tinh cho mot buoc doi khu")
        return plan, None

    buoc_moi = Task(
        task_id=moi,
        tool=_CHANGE_TOOL,
        depends_on=[source_task_id],
        # `booking_id` là LITERAL đọc từ database, không phải `InputRef`: bước
        # nguồn đã SUCCESS từ lượt trước nên kết quả của nó không còn nằm trong
        # RAM của lượt này, và một `InputRef` trỏ vào đó sẽ chết
        # `DEPENDENCY_ERROR` ngay sau khi đơn vị vừa đồng ý.
        input={"booking_id": booking_id, _ZONE_FIELD: khu_moi},
    )

    tasks: list[Task] = []
    for task in plan.tasks:
        if task.task_id == source_task_id:
            # Bước đã chạy giữ đúng ô nó đã chạy — `_apply_user_answers` vừa vá
            # `parking_zone` mới vào đây, và để nguyên nghĩa là màn hình duyệt
            # cùng trang chi tiết kể lại một chuyện chưa từng xảy ra.
            tasks.append(
                Task(
                    task_id=task.task_id,
                    tool=task.tool,
                    depends_on=list(task.depends_on),
                    input=dict(_persisted_input(rows[task.task_id]) or task.input),
                )
            )
            tasks.append(buoc_moi)
            continue
        tasks.append(task)

    await repository.create_task(
        workflow_id,
        {
            "id": buoc_moi.task_id,
            "tool": buoc_moi.tool,
            "depends_on": list(buoc_moi.depends_on),
            "input": dict(buoc_moi.input),
            "status": TaskStatus.PENDING.value,
        },
    )
    logger.info("mo mot buoc doi khu %s -> %s", source_task_id, moi)
    return TaskPlan(goal=plan.goal, tasks=tasks), moi


def has_completed_zone_change(rows: list[dict[str, Any]]) -> bool:
    """Có bước đổi khu nào vừa chạy xong không."""
    return any(
        str(row.get("tool")) == _CHANGE_TOOL and str(row.get("status")) == TaskStatus.SUCCESS.value for row in rows
    )


async def repin_payment_after_zone_change(repository: Any, workflow_id: str, plan: TaskPlan | None) -> bool:
    """Ghim lại thẻ chờ thanh toán theo giá của khu MỚI.

    Vì sao không để `persist_pending_approval` tự lo: nó đọc báo giá từ
    `quote_from_results`, và tập kết quả ấy chứa cả `book_parking` được SEED
    lại từ lượt trước — với `amount` của khu CŨ. Con số đầu tiên khớp ba field
    `booking_id`/`amount`/`currency` sẽ thắng, và đó là con số sai.

    Ở đây thì báo giá đi theo đúng đường provenance của `pay_fee` và lấy
    `amount` từ chính `parking_bookings` — nguồn CÓ THẨM QUYỀN, đã được
    `change_booking_zone` tính lại theo `ZONE_PRICES`.

    `save_pending_approval` là chỗ DUY NHẤT được ghi ba thứ ấy, và nó cập nhật
    tại chỗ khi thẻ còn `AWAITING`. Nhờ vậy đổi khu hai lần liên tiếp vẫn chỉ
    có MỘT thẻ, mang con số mới nhất — không cộng dồn, không để lại thẻ mồ côi.

    Không có bước đổi khu nào chạy xong thì đây là một lệnh KHÔNG LÀM GÌ.
    """
    if plan is None:
        return False
    task_id = payment_task_id(plan)
    if task_id is None:
        return False
    rows = await repository.list_tasks(workflow_id)
    if not has_completed_zone_change(rows):
        return False
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    quote = await quote_from_persisted_book_parking(pool, workflow_id, task_id)
    if quote is None:
        logger.warning("doi khu xong nhung khong dung lai duoc bao gia")
        return False
    return await save_pending_approval(pool, workflow_id=workflow_id, task_id=task_id, quote=quote)
