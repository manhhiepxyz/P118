"""Hàng đợi duyệt của ĐƠN VỊ CUNG CẤP — cho mọi dịch vụ.

`viewing_approval_routes` phục vụ riêng lịch tham quan. File này phục vụ sáu
dịch vụ còn lại: đăng ký xe, chỗ đỗ, bảo trì, chuyển nhà, xe đưa đón, đăng ký
tư vấn. Trước đó chúng chạy thẳng — khách nhận kết quả trước khi có ai bên kia
đồng ý.

Quyền: `provider` hoặc `admin`, cùng cách cổng tham quan đang làm. Người dùng
cuối KHÔNG được tự duyệt phần của mình — đó là lý do cổng tồn tại.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.deps import require_roles
from src.config import get_settings
from src.orchestration.demo_service import ResumeError, resume_after_service_decision
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.service_approval import (
    list_awaiting,
    list_by_status,
    pending_for_workflow,
    record_service_decision,
)

def _as_dict(value: Any) -> dict[str, Any]:
    """JSONB → dict. asyncpg trả chuỗi khi không cài codec."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


router = APIRouter(prefix="/service-approvals", tags=["service-approvals"])


class _DecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(approve|reject)$")
    reject_reason: str | None = Field(default=None, max_length=500)


@router.get("", summary="Hàng đợi duyệt của đơn vị (mọi dịch vụ)")
async def list_service_approvals(
    # 200, không phải 50. Hàng đợi duyệt của một khu đô thị dài hơn 50 là
    # bình thường, và phần bị cắt luôn là phần MỚI NHẤT — đúng thứ vừa có
    # người đang chờ.
    limit: int = 200,
    status: str = "AWAITING",
    _reviewer: dict = Depends(require_roles("provider")),
) -> dict[str, Any]:
    """`status=AWAITING` (mặc định) là hàng đợi; `status=decided` là lịch sử.

    Mặc định phải là hàng đợi: đó là thứ người duyệt mở màn này để làm. Lịch sử
    là chỗ tra lại, và tra lại là việc hiếm hơn nhiều.
    """
    wanted: tuple[str, ...]
    if status == "decided":
        wanted = ("APPROVED", "REJECTED", "EXPIRED")
    elif status in {"APPROVED", "REJECTED", "EXPIRED", "AWAITING"}:
        wanted = (status,)
    else:
        raise HTTPException(status_code=422, detail="Trạng thái không hợp lệ.")

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        rows = (
            await list_awaiting(pool, limit=limit)
            if wanted == ("AWAITING",)
            else await list_by_status(pool, wanted, limit=limit)
        )
        # TỔNG số, không chỉ số đang hiển thị.
        #
        # Thiếu nó thì một hàng đợi dài hơn `limit` trông y hệt một hàng đợi
        # vừa đủ, và mục mới — xếp cuối vì cũ-nhất-trước — nằm ngoài tầm nhìn
        # mà không dấu hiệu nào. Đo được: yêu cầu vào hàng đợi lúc 18:44:41
        # ở vị trí ~62/50; người duyệt không thấy, khách huỷ.
        async with pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT count(*) FROM service_approvals WHERE status = ANY($1::varchar[])",
                list(wanted),
            )
    finally:
        await pool.close()
    return {
        "total": int(total or 0),
        "items": [
            {
                "workflow_id": str(row["workflow_id"]),
                "task_id": row["task_id"],
                "tool": row["tool"],
                "service_label": row["service_label"],
                # asyncpg trả JSONB dưới dạng CHUỖI, không phải dict. Trả
                # thẳng ra thì client nhận một chuỗi JSON, và mọi vòng lặp trên
                # nó chạy qua từng KÝ TỰ — giao diện vẽ ra một danh sách ký tự
                # thay vì các dữ kiện.
                "details": _as_dict(row["details"]),
                "applicant_name": row.get("applicant_name"),
                "applicant_phone": row.get("applicant_phone"),
                "status": row.get("status", "AWAITING"),
                "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                "decided_by": row.get("decided_by"),
                "decided_at": row["decided_at"].isoformat() if row.get("decided_at") else None,
                "reject_reason": row.get("reject_reason"),
            }
            for row in rows
        ]
    }


@router.post("/{workflow_id}/{task_id}/decide", summary="Duyệt hoặc từ chối MỘT bước")
async def decide_service_approval(
    workflow_id: str,
    task_id: str,
    body: _DecideBody,
    reviewer: dict = Depends(require_roles("provider")),
) -> dict[str, Any]:
    """Quyết định một bước, rồi chạy tiếp NẾU không còn bước nào đang chờ.

    Quyết định theo TỪNG BƯỚC vì một yêu cầu có thể gồm nhiều dịch vụ của nhiều
    đơn vị, và mỗi đơn vị chỉ quyết định phần của mình.

    `decided_by` lấy từ JWT của người duyệt, KHÔNG nhận từ body: một trường
    trong body nghĩa là ai cũng ký tên người khác được.

    Double-decide bị chặn ở SQL (`WHERE status='AWAITING'`), nên lệnh thứ hai
    nhận 409 thay vì lặng lẽ ghi đè quyết định thứ nhất.
    """
    if body.decision == "reject" and not body.reject_reason:
        raise HTTPException(status_code=422, detail="Từ chối cần lý do.")

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        changed = await record_service_decision(
            pool,
            workflow_id,
            task_id,
            "APPROVED" if body.decision == "approve" else "REJECTED",
            decided_by=reviewer["username"],
            reason=body.reject_reason,
        )
        if not changed:
            existing = [r for r in await pending_for_workflow(pool, workflow_id) if r["task_id"] == task_id]
            if not existing:
                raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu duyệt này.")
            raise HTTPException(status_code=409, detail="Bước này đã được quyết định trước đó.")
    finally:
        await pool.close()

    # Response đang cache trong `_DEMO_JOBS` được dựng lúc còn chờ duyệt. Không
    # bỏ đi thì mọi lượt poll sau vẫn trả "đang chờ" dù database đã đổi, và
    # giao diện mắc kẹt vĩnh viễn ở màn chờ.
    from src.api.routes import _DEMO_JOBS, request_fresh_answer

    job = _DEMO_JOBS.get(workflow_id)
    if job is not None:
        job["response"] = None

    settings = get_settings()
    try:
        outcome = await resume_after_service_decision(
            workflow_id,
            resident_url=settings.resident_service_url,
            transport_url=settings.transport_service_url,
            payment_url=settings.payment_service_url,
            property_url=settings.property_service_url,
            resident_services_url=settings.resident_services_service_url,
            tour_url=settings.tour_service_url,
            consultation_url=settings.consultation_service_url,
            shuttle_url=settings.shuttle_service_url,
        )
    except ResumeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Tình huống vừa đổi tay. Không xin câu mới thì khách vừa được duyệt xong
    # vẫn đọc "đang chờ đơn vị cung cấp dịch vụ xác nhận" — trong khi bước tiếp
    # theo đã là xác nhận thanh toán của chính họ.
    request_fresh_answer(workflow_id, job=job)

    return {"workflow_id": workflow_id, "task_id": task_id, "decision": body.decision, **outcome}
