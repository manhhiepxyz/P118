"""Release-on-failure — compensation tối thiểu cho P-118 (Phase B/C).

Chính sách (đã chốt với người dùng, KHÔNG full saga):

  - Một workflow đi tới terminal FAILED/CANCELLED DO MÁY (fail giữa chuỗi,
    lỗi dịch vụ, zombie sweep) thì các side-effect "có thể đảo ngược an toàn"
    được release: `book_parking` → refund (nếu đã PAID) → huỷ booking (capacity
    về). `register_resident`/`register_vehicle` cố ý GIỮ — idempotent, là
    business record hợp lệ, xoá sẽ phá constraint và mất vết.
  - User REJECT thanh toán KHÔNG BAO GIỜ kích hoạt release: `reject_payment`
    (demo_service) + `REJECT_KEEPS_BOOKING=True` giữ booking. Ranee này được
    enforce tại CALL SITE (không có flag ở đây): release chỉ chạy từ những nơi
    đã biết workflow terminal do máy — sau `_run_demo_job` (mục B.3a của plan)
    và từ sweeper (B.4).

Nguyên tắc bất biến payment idempotency: release CHỈ chạy từ terminal
FAILED/CANCELLED — không còn retry cùng booking (booking đã DELETE, capacity
về, workflow DONE). Không bao giờ gọi release trên workflow đang RUNNING hay
user REJECT.

Tất cả đều best-effort + idempotent: chạy lại vô hại, lỗi DB không raise ra
caller (workflow đã terminal rồi — thất bại ở đây không thể làm tồi hơn).
"""

from __future__ import annotations

import logging
from typing import Any

from src.db.parking_payment_repository import cancel_booking, refund_payment
from src.orchestration.deps import build_repository

logger = logging.getLogger(__name__)

# Workflow terminal do máy — đây là TẬP HỢP DUY NHẤT mà release được phép chạy.
TERMINAL_RELEASE_STATUSES: frozenset[str] = frozenset({"FAILED", "CANCELLED"})

# Side-effect có thể release an toàn. register_resident/register_vehicle cố ý
# KHÔNG nằm trong đây (Phase C ranee).
_RELEASABLE_TOOLS: frozenset[str] = frozenset({"book_parking", "pay_fee"})

# Task đã kết thúc vĩnh viễn — không đổi trạng thái nữa.
_TERMINAL_TASK_STATUSES: frozenset[str] = frozenset(
    {"SUCCESS", "FAILED", "CANCELLED", "SKIPPED"}
)


async def release_on_failure(workflow_id: str) -> dict[str, Any]:
    """Release side-effect của workflow terminal FAILED/CANCELLED.

    Đọc mọi thứ từ DB (pattern `resume_payment_after_approval`): không dựa
    vào `_DEMO_JOBS`, không exception object. Chạy được sau restart.

    Quy trình cho một workflow đáng release:
      1. book_parking SUCCESS → booking_id từ result_data → nếu có payment PAID
         thì refund trước, rồi cancel booking (capacity về).
      2. pay_fee SUCCESS → refund payment của booking (pay_fee luôn phụ thuộc
         book_parking, nên booking_id lấy từ task book_parking).

    Trả về tóm tắt những gì đã làm (để test + log). KHÔNG raise ra caller.
    """
    result: dict[str, Any] = {"workflow_id": workflow_id, "released": False}
    repository = await build_repository(migrate=False)
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        try:
            record = await repository.get_workflow(workflow_id)
            workflow = record["workflow"]
        except Exception:  # noqa: BLE001 - workflow không tồn tại thì không làm gì
            return result
        if workflow.get("status") not in TERMINAL_RELEASE_STATUSES:
            return result

        booking_ids: set[str] = set()
        payment_ids: set[str] = set()
        for row in await repository.list_tasks(workflow_id):
            if row.get("status") != "SUCCESS":
                continue
            tool = row.get("tool")
            if tool not in _RELEASABLE_TOOLS:
                continue
            data = row.get("result_data") or {}
            if not isinstance(data, dict):
                continue
            booking_id = data.get("booking_id")
            if booking_id:
                booking_ids.add(booking_id)
            payment_id = data.get("payment_id")
            if payment_id:
                payment_ids.add(payment_id)

        # Refund payment PAID trước khi cancel booking (FK payments → bookings).
        refunded: list[str] = []
        for booking_id in sorted(booking_ids):
            if await refund_payment(pool, booking_id):
                refunded.append(booking_id)

        cancelled: list[str] = []
        for booking_id in sorted(booking_ids):
            if await cancel_booking(pool, booking_id):
                cancelled.append(booking_id)
                result["released"] = True

        result["refunded_booking_ids"] = refunded
        result["cancelled_booking_ids"] = cancelled
        if result["released"]:
            logger.info(
                "release_on_failure(%s): refund=%s cancelled=%s",
                workflow_id,
                result.get("refunded", []),
                cancelled,
            )
        return result
    except Exception:  # noqa: BLE001 - release không được làm vỡ caller
        logger.warning("release_on_failure(%s) failed", workflow_id, exc_info=True)
        return result
    finally:
        await pool.close()
