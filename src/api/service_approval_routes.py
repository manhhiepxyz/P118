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
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.api.deps import require_roles
from src.config import get_settings
from src.orchestration.demo_service import ResumeError, resume_after_service_decision
from src.orchestration.runtime_provider import acquire_repository
from src.orchestration.service_approval import (
    REJECT_CODES,
    allowed_reject_codes,
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


# Mã nguyên nhân ĐÓNG. Máy đọc mã, người đọc câu chữ.
#
# Không có mã thì cách duy nhất để biết "hết chỗ" là đọc `reject_reason` —
# tức biến chính tả của người duyệt thành logic nghiệp vụ. Một `LIKE '%hết
# chỗ%'` hỏng ngay lần đầu ai đó gõ "không còn slot", và khi nó hỏng thì khách
# mất đường sửa mà không ai biết.
_CONTROL_CHARS = {c: None for c in range(32) if c not in (9, 10, 13)}


def _sach(text: str) -> str:
    """Cắt ký tự điều khiển và khoảng trắng thừa khỏi câu người duyệt gõ.

    Câu này đi thẳng ra màn hình của khách, nên nó phải là văn bản thuần. Không
    lọc nội dung nghiệp vụ: người duyệt được quyền viết bất cứ điều gì họ cần
    nói, và hệ thống không đọc câu ấy để quyết định gì cả.
    """
    return " ".join(text.translate(_CONTROL_CHARS).split())


class _DecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(approve|reject)$")
    reject_code: Literal[REJECT_CODES] | None = Field(default=None)  # type: ignore[valid-type]
    reject_reason: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def _mot_quyet_dinh_khong_mang_ca_hai_nghia(self) -> _DecideBody:
        """Duyệt thì không kèm lý do từ chối; từ chối thì phải có ĐỦ cả hai.

        Một lệnh `approve` mang `reject_code` là một quyết định tự mâu thuẫn —
        nhận nó nghĩa là để hai nửa của cùng một bản ghi nói hai điều khác nhau,
        và tầng dưới sẽ phải đoán nửa nào đúng.
        """
        if self.decision == "approve":
            if self.reject_code is not None or self.reject_reason is not None:
                raise ValueError("Quyết định duyệt không mang lý do từ chối.")
            return self
        if self.reject_code is None:
            raise ValueError("Từ chối cần một nguyên nhân trong danh sách.")
        reason = _sach(self.reject_reason or "")
        if not reason:
            raise ValueError("Từ chối cần lý do cho người dùng đọc.")
        object.__setattr__(self, "reject_reason", reason)
        return self


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
                "reject_code": row.get("reject_code"),
                # UI không tự duy trì một bản sao policy theo tool. Nếu sau
                # này thêm dịch vụ/mã mới, backend vẫn là nguồn duy nhất.
                "allowed_reject_codes": list(allowed_reject_codes(str(row["tool"]))),
            }
            for row in rows
        ],
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
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        existing = [r for r in await pending_for_workflow(pool, workflow_id) if r["task_id"] == task_id]
        if not existing:
            raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu duyệt này.")
        target = existing[0]
        if body.decision == "reject" and body.reject_code not in allowed_reject_codes(str(target["tool"])):
            # Fail trước khi ghi quyết định. Trả 422 vì body hợp lệ về hình
            # dạng nhưng không hợp lệ với loại dịch vụ đang được quyết định.
            raise HTTPException(status_code=422, detail="Nguyên nhân từ chối không áp dụng cho dịch vụ này.")
        changed = await record_service_decision(
            pool,
            workflow_id,
            task_id,
            "APPROVED" if body.decision == "approve" else "REJECTED",
            decided_by=reviewer["username"],
            reason=body.reject_reason,
            reject_code=body.reject_code,
        )
        if not changed:
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
    #
    # TRỪ khi lượt này vừa mở một vòng sửa lỗi: câu chốt lúc đó CHÍNH LÀ lý do
    # đơn vị vừa gõ, đã được ghim kèm hướng dẫn tất định. Xin câu mới ở đây sẽ
    # xoá nó và nhờ một mô hình viết lại một quyết định nghiệp vụ — người duyệt
    # là người duy nhất biết vì sao họ từ chối.
    if not outcome.get("repair_pending"):
        request_fresh_answer(workflow_id, job=job)

    return {"workflow_id": workflow_id, "task_id": task_id, "decision": body.decision, **outcome}
