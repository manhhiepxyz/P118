"""Endpoint quản trị — gán liên kết tài khoản ↔ cư dân.

Đây là đường DUY NHẤT ghi vào `user_resident_links`. Không có endpoint tương
ứng cho customer, và đó là chủ ý: nếu người dùng tự khẳng định được mình sở
hữu một căn hộ thì toàn bộ mô hình quyền cư dân chỉ còn là một biểu mẫu.

Trong hệ thống thật, chỗ này là nơi kết quả xác minh của provider/ban quản lý
được ghi lại. Backend chỉ ĐỌC trạng thái đó — không thực hiện eKYC, không đọc
CCCD, không so khuôn mặt.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import require_roles
from src.api.schemas import AdminResidentLinkRequest, AdminResidentLinkResponse
from src.db.resident_link_repository import VerificationStatus, upsert_link
from src.orchestration.deps import build_repository

router = APIRouter(prefix="/admin", tags=["admin"])

# Message dùng chung cho mọi trường hợp "không tìm thấy". Phân biệt "user không
# tồn tại" với "resident không tồn tại" biến endpoint này thành công cụ dò danh
# bạ: gửi ID bất kỳ, đọc thông báo, biết ID nào có thật.
_NOT_FOUND = "Không tìm thấy dữ liệu phù hợp."


@router.post(
    "/resident-links/{user_id}",
    response_model=AdminResidentLinkResponse,
    summary="Gán/cập nhật liên kết cư dân cho một tài khoản",
)
async def upsert_resident_link(
    user_id: str,
    request: AdminResidentLinkRequest,
    _admin: dict = Depends(require_roles("admin")),
) -> AdminResidentLinkResponse:
    """Ghi trạng thái xác minh do admin/provider quyết định.

    `verified_at` chỉ được đặt khi status là VERIFIED, và bị xoá khi chuyển về
    PENDING/REJECTED — một mốc thời gian "đã xác minh" còn sót lại trên một
    liên kết đã bị từ chối là bằng chứng sai lệch trong audit trail.
    """
    repository = await build_repository(migrate=False)
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        exists = await pool.fetchrow(
            """
            SELECT
                (SELECT 1 FROM users WHERE id = $1::uuid AND archived_at IS NULL) AS has_user,
                (SELECT 1 FROM residents WHERE resident_id = $2) AS has_resident
            """,
            _safe_uuid(user_id),
            request.resident_id,
        )
        if exists is None or exists["has_user"] is None or exists["has_resident"] is None:
            raise HTTPException(status_code=404, detail=_NOT_FOUND)

        await upsert_link(
            pool,
            user_id=user_id,
            resident_id=request.resident_id,
            verification_status=VerificationStatus(request.verification_status),
        )
    finally:
        await pool.close()

    return AdminResidentLinkResponse(
        user_id=user_id,
        verification_status=request.verification_status,
    )


def _safe_uuid(value: str):
    """`user_id` sai định dạng phải thành 404, không phải 500.

    Một ValueError chưa bắt ở đây trả 500 kèm traceback, và traceback là nơi
    giá trị vừa gửi bị ghi lại nguyên văn.
    """
    import uuid

    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(status_code=404, detail=_NOT_FOUND) from None
