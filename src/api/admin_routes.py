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
from src.api.schemas import AdminResidentLinkRequest, AdminResidentLinkResponse, LinkRequestDecision
from src.db.link_request_repository import decide_request, list_requests
from src.db.resident_link_repository import VerificationStatus, upsert_link
from src.orchestration.runtime_provider import acquire_repository

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
    repository = await acquire_repository()
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


@router.get(
    "/resident-link-requests",
    summary="Danh sách yêu cầu liên kết căn hộ đang chờ duyệt",
)
async def list_resident_link_requests(
    status: str = "PENDING",
    _admin: dict = Depends(require_roles("admin")),
) -> dict:
    """Admin không còn phải tự biết UUID của ai muốn liên kết căn hộ nào.

    Tên hiển thị đã mask: admin cần đối chiếu chứ không cần bản đầy đủ trên một
    danh sách, và danh sách thì hay được mở trên màn hình chung.
    """
    if status not in {"PENDING", "APPROVED", "REJECTED"}:
        raise HTTPException(status_code=422, detail="Trạng thái không hợp lệ.")

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        return {"items": await list_requests(pool, status=status)}
    finally:
        await pool.close()


@router.post(
    "/resident-link-requests/{request_id}/decision",
    summary="Duyệt hoặc từ chối một yêu cầu liên kết căn hộ",
)
async def decide_resident_link_request(
    request_id: str,
    request: LinkRequestDecision,
    admin: dict = Depends(require_roles("admin")),
) -> dict:
    """Duyệt = tạo/nối hồ sơ cư dân và mở quyền, trong MỘT transaction.

    Không nhận `user_id` hay `resident_id` từ body: cả hai đọc từ dòng yêu cầu.
    Nhận từ body nghĩa là một request có thể duyệt yêu cầu này nhưng gán quyền
    cho một tài khoản khác.

    Yêu cầu không còn PENDING trả 409 chứ không phải 404: nó có thật, chỉ là đã
    được xử lý. 404 ở đây sẽ khiến admin nghĩ mình gõ nhầm mã.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        resident_id = await decide_request(
            pool,
            _safe_uuid(request_id),
            approve=request.decision == "approve",
            admin_user_id=admin["id"],
        )
        if request.decision == "approve" and resident_id is None:
            raise HTTPException(status_code=409, detail="Yêu cầu này đã được xử lý.")
    finally:
        await pool.close()

    # KHÔNG trả `resident_id`: nó là mã nội bộ, và response này đi qua browser.
    return {"request_id": request_id, "decision": request.decision}
