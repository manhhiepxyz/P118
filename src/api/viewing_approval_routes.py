"""Yêu cầu tham quan chờ duyệt — provider/admin quyết định qua cổng /review.

Path song song với `verification_routes.py` (xác thực căn hộ/xe): cùng người
duyệt (provider/admin), cùng cổng /review, nhưng nguồn dữ liệu KHÁC.

  - verification: record nằm ở Mock Ownership Provider (8004), main app chỉ
    materialize khi duyệt.
  - viewing: yêu cầu nằm thẳng trong PostgreSQL (`viewing_approvals`) — Tour
    provider (8005) CHƯA biết lịch cho tới khi được duyệt, vì workflow DỪNG ở
    bước `schedule_property_viewing` để hỏi người. Duyệt = gọi Tour materialize
    lịch (lấy `viewing_id` + 4 thông tin người đón tiếp) rồi chạy nốt các bước
    phụ thuộc (`book_shuttle`, ~30s).

Điểm khác so với verification (tải):

  - Duyệt KHÔNG chỉ đổi status: nó chạy cả phần DAG còn lại qua Executor, vì
    `schedule_property_viewing` là bước TRƯỚC của `book_shuttle` chứ không phải
    bước cuối. Vì vậy route này gọi `resume_viewing_after_approval` — đồng bộ,
    mất ~30s (đặt xe). UI đang hiện "Đang xử lý…".
  - Từ chối đánh FAILED cả chuỗi (viewing + downstream), không giữ gì cả —
    không có "chỗ đỗ" để giữ như payment.

Ranh giới tin cậy:

  - Browser KHÔNG gửi `status`/`decided_by`/`reject_reason` cho quyết định duyệt;
    chỉ gửi `{decision, reject_reason?}`. `decided_by` lấy từ JWT của người duyệt.
  - Chỉ provider/admin vào được (`require_roles`). Khách đọc trạng thái workflow
    của mình qua GET /workflows/demo/{id} — field `viewing_approval` KHÔNG chứa
    PII của người yêu cầu.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.deps import require_roles
from src.api.routes import _DEMO_JOBS, request_fresh_answer
from src.config import get_settings
from src.orchestration.demo_service import reject_viewing, resume_viewing_after_approval
from src.orchestration.viewing_approval import expire_stale_viewing_approvals
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.viewing_approval import list_viewing_approvals

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/viewing-approvals", tags=["viewing-approvals"])

_STATUSES = {"AWAITING", "APPROVED", "REJECTED", "EXPIRED"}


class _DecideBody(BaseModel):
    decision: str = Field(..., pattern="^(approve|reject)$")
    reject_reason: str | None = Field(default=None, max_length=500)


def _pending_to_dict(pending) -> dict:
    """Bản chép công khai cho người duyệt (gồm PII người yêu cầu — reviewer)."""
    return {
        "workflow_id": pending.workflow_id,
        "task_id": pending.task_id,
        "status": pending.status,
        "project_id": pending.project_id,
        "project_name": pending.project_name,
        "viewing_date": pending.viewing_date,
        "viewing_time": pending.viewing_time,
        "passenger_count": pending.passenger_count,
        "wants_shuttle": pending.wants_shuttle,
        "applicant_name": pending.applicant_name,
        "applicant_phone": pending.applicant_phone,
        "reject_reason": pending.reject_reason,
        "decided_by": pending.decided_by,
    }


@router.get("", summary="Danh sách yêu cầu lịch tham quan (cho người duyệt)")
async def list_viewing_approval_records(
    status: str | None = None,
    _reviewer: dict = Depends(require_roles("provider", "admin")),
) -> dict:
    """Danh sách yêu cầu tham quan cho cổng /review — mới nhất trước.

    `status` lọc theo vòng đời quyết định (AWAITING/APPROVED/REJECTED); bỏ qua
    khi None (mặc định hiện cả ba cho tab "Lịch sử").
    """
    if status is not None and status not in _STATUSES:
        raise HTTPException(status_code=422, detail="Trạng thái không hợp lệ.")

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        # Dọn hàng chờ TRƯỚC khi trả danh sách.
        #
        # Người duyệt không có cách nào biết một yêu cầu đã hết hiệu lực chỉ
        # bằng cách nhìn: nó trông y hệt yêu cầu hợp lệ. Bấm Duyệt xong mới vỡ
        # ở Tour provider, và lỗi trả về là 502 — không nói được gì cho người
        # đang đứng trước màn hình. Lọc ở đây thì thứ không duyệt được không
        # bao giờ xuất hiện như thể duyệt được.
        await expire_stale_viewing_approvals(pool)
        items = await list_viewing_approvals(pool, status)
    finally:
        await pool.close()
    return {"items": [_pending_to_dict(item) for item in items]}


@router.post("/{workflow_id}/decide", summary="Duyệt hoặc từ chối một lịch tham quan")
async def decide_viewing_approval(
    workflow_id: str,
    body: _DecideBody,
    reviewer: dict = Depends(require_roles("provider", "admin")),
) -> dict:
    """Provider/admin quyết định lịch tham quan.

    Duyệt → `resume_viewing_after_approval` (đồng bộ, ~30s): materialize lịch
    qua Tour provider, ghi kết quả, chạy nốt `book_shuttle`. Từ chối →
    `reject_viewing` đánh FAILED chuỗi + workflow kèm lý do.

    `decided_by` lấy từ JWT của người duyệt (main app đặt, không nhận từ body).
    Double-decide bị chặn bởi `WHERE status='AWAITING'` → ResumeError ALREADY_DECIDED.
    """
    settings = get_settings()

    # Response đang cache trong `_DEMO_JOBS` được dựng lúc workflow còn chờ
    # duyệt. Sau quyết định, nó là ảnh cũ: nếu không bỏ đi, mọi lần poll tiếp
    # theo vẫn trả "chờ đơn vị xác nhận" dù database đã ghi SUCCESS/FAILED, và
    # giao diện mắc kẹt vĩnh viễn ở màn chờ (mirror payment route).
    job = _DEMO_JOBS.get(workflow_id)
    if job is not None:
        job["response"] = None

    if body.decision == "reject":
        if not body.reject_reason:
            raise HTTPException(status_code=422, detail="Từ chối cần lý do.")
        try:
            await reject_viewing(workflow_id, body.reject_reason, decided_by=reviewer["username"])
        except Exception as exc:  # noqa: BLE001 - lỗi map ra HTTP bên dưới
            raise _to_http(exc) from exc
        request_fresh_answer(workflow_id, job=job)
        return {
            "workflow_id": workflow_id,
            "decision": "reject",
            "status": "REJECTED",
            "summary": "Đã từ chối lịch tham quan. Khách sẽ thấy lý do ở trạng thái workflow.",
        }

    try:
        outcome = await resume_viewing_after_approval(
            workflow_id,
            tour_url=settings.tour_service_url,
            shuttle_url=settings.shuttle_service_url,
            resident_url=settings.resident_service_url,
            transport_url=settings.transport_service_url,
            payment_url=settings.payment_service_url,
            property_url=settings.property_service_url,
            resident_services_url=settings.resident_services_service_url,
            consultation_url=settings.consultation_service_url,
            decided_by=reviewer["username"],
        )
    except Exception as exc:  # noqa: BLE001 - lỗi map ra HTTP bên dưới
        raise _to_http(exc) from exc

    viewing_result = outcome["viewing_result"]
    if not viewing_result.success:
        # Materialize đã thất bại → workflow đã bị đánh FAILED bên trong
        # `_materialize_and_run_remaining`; báo cho người duyệt lỗi an toàn.
        raise HTTPException(
            status_code=502,
            detail="Xác nhận lịch tham quan thất bại khi hoàn tất duyệt. Vui lòng thử lại.",
        )

    shuttle_results = [r for r in outcome["task_results"].values() if r.data]
    shuttle_summary = ""
    for result in shuttle_results:
        data = result.data or {}
        if "driver_name" in data:
            shuttle_summary = (
                f" Xe đã đặt: tài xế {data.get('driver_name')}, "
                f"biển số {data.get('license_plate')}, {data.get('vehicle_type')}, "
                f"giờ đón {data.get('pickup_time')}."
            )
            break

    logger.info("viewing approved workflow=%s reviewer=%s", workflow_id, reviewer["username"])
    # Tình huống vừa đổi: lịch đã được duyệt. Câu cũ nói "đơn vị tour đang xác
    # nhận" và nó hết đúng ngay tại đây.
    request_fresh_answer(workflow_id, job=job)
    return {
        "workflow_id": workflow_id,
        "decision": "approve",
        "status": "APPROVED",
        "summary": f"Đã duyệt lịch tham quan.{shuttle_summary}",
    }


def _to_http(exc: Exception) -> HTTPException:
    """Map lỗi resume/materialize thành HTTPException với message an toàn.

    `ResumeError` mang message viết sẵn cho người dùng cuối (không chứa SQL,
    payload hay tên bảng); lỗi không mong đợi thì nói chung chung, không echo
    exception thật (có thể chứa URL/stack nội bộ).
    """
    code = getattr(exc, "code", None)
    if code is not None:
        status = {
            "NOT_FOUND": 404,
            "ALREADY_DECIDED": 409,
            "MATERIALIZE_FAILED": 502,
        }.get(code, 409)
        return HTTPException(status_code=status, detail=str(exc))

    logger.exception("viewing approve/reject unexpected error")
    return HTTPException(status_code=502, detail="Xử lý yêu cầu tham quan thất bại. Vui lòng thử lại.")
