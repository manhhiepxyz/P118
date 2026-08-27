"""Khách xác nhận ĐỀ XUẤT ĐƠN VỊ — một nút, một quyết định, một lần.

Client gửi ĐÚNG hai thứ: mã đề xuất (ở đường dẫn) và quyết định (ở body). Không
provider, không số tiền, không tên đơn vị. Mọi dữ kiện khác đọc từ database bên
trong transaction — vì mọi thứ nhận từ body là thứ người gọi tự khai, và một
trường được nhận thì sớm muộn sẽ có người tin nó.

`extra="forbid"` không phải để bắt lỗi chính tả: nó là hàng rào cho luật ấy.
Một client gửi kèm `{"service_provider_id": "MOV-03", "amount": 1000}` phải
nhận 422 chứ không phải một lượt xác nhận lặng lẽ bỏ qua hai trường thừa —
lặng lẽ bỏ qua nghĩa là lần sau ai đó nối chúng vào là không ai thấy gì đổi.

CHỈ `customer`. Đơn vị và admin không xác nhận thay khách: khoản tiền là của
khách, và người duyệt bên kia có bề mặt riêng (`/service-approvals`). Đây cũng
là lý do route này không nằm chung file với hàng đợi duyệt.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.api.deps import require_roles
from src.db.proposal_repository import doc_de_xuat, xac_nhan_de_xuat
from src.db.quote_repository import doc_bao_gia
from src.orchestration.provider_directory import ten_don_vi
from src.orchestration.runtime_provider import acquire_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/service-proposals", tags=["service-proposals"])

# Mã kết quả → mã HTTP. Bảng CHỨ KHÔNG phải chuỗi `if`: thêm một kết quả mà
# quên ánh xạ thì `KeyError` ở đây, chứ không rơi vào một nhánh mặc định trả
# 200 cho một việc chưa xảy ra.
_HTTP = {
    "CONFIRMED": 200,
    # 404 dùng chung cho "không có" và "không phải của bạn" — phân biệt chúng
    # là xác nhận với người đang dò rằng một mã nào đó có thật.
    "NOT_FOUND": 404,
    "ALREADY_DECIDED": 409,
    "QUOTE_EXPIRED": 409,
    "QUOTE_NOT_USABLE": 409,
}

_THONG_DIEP = {
    "NOT_FOUND": "Không tìm thấy đề xuất này.",
    "ALREADY_DECIDED": "Đề xuất này đã được xử lý trước đó.",
    "QUOTE_EXPIRED": "Báo giá đã hết hiệu lực. Bạn để mình hỏi lại giá mới nhé.",
    "QUOTE_NOT_USABLE": "Báo giá này không còn dùng được.",
}


class _ConfirmBody(BaseModel):
    """Đúng MỘT trường. Mọi thứ khác bị từ chối, không phải bỏ qua."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["confirm"] = Field(
        description="Chỉ `confirm`. Từ chối một đề xuất là xin báo giá khác, không phải một quyết định ở đây."
    )


async def _cong_khai(pool: Any, de_xuat: Any) -> dict[str, Any]:
    """Hình dạng công khai của một đề xuất. Giá và đơn vị đọc từ CHỨNG TỪ.

    Đề xuất không giữ bản sao của chúng (xem `service_provider_proposals`), nên
    đây là chỗ hai mảnh được ghép lại — và ghép lúc ĐỌC nghĩa là không bao giờ
    có một bản sao cũ để lệch.
    """
    bao_gia = await doc_bao_gia(pool, de_xuat.quote_id)
    return {
        "proposal_id": de_xuat.proposal_id,
        "workflow_id": de_xuat.workflow_id,
        "task_id": de_xuat.task_id,
        "status": de_xuat.status,
        "provider": (
            {
                "id": bao_gia.service_provider_id,
                "name": ten_don_vi(bao_gia.service_provider_id),
            }
            if bao_gia
            else None
        ),
        "amount": bao_gia.amount if bao_gia else None,
        "currency": bao_gia.currency if bao_gia else None,
        "valid_until": bao_gia.valid_until.isoformat() if bao_gia else None,
    }


@router.get("/{proposal_id}", summary="Xem một đề xuất đơn vị")
async def get_proposal(
    proposal_id: str,
    user: dict = Depends(require_roles("customer")),
) -> dict[str, Any]:
    """Chỉ CHỦ của yêu cầu xem được. Người khác nhận 404, không phải 403.

    Cùng lý do với đường xác nhận: 403 xác nhận rằng mã ấy có thật.
    """
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        de_xuat = await doc_de_xuat(pool, proposal_id)
        if de_xuat is None:
            raise HTTPException(status_code=404, detail=_THONG_DIEP["NOT_FOUND"])
        chu = await pool.fetchval(
            "SELECT owner_user_id FROM workflows WHERE workflow_id = $1::uuid", de_xuat.workflow_id
        )
        if chu is None or str(chu) != str(user["id"]):
            logger.info("chặn đọc đề xuất ngoài quyền sở hữu")
            raise HTTPException(status_code=404, detail=_THONG_DIEP["NOT_FOUND"])
        return await _cong_khai(pool, de_xuat)
    finally:
        await pool.close()


@router.post("/{proposal_id}/confirm", summary="Khách đồng ý với đơn vị được đề xuất")
async def confirm_proposal(
    proposal_id: str,
    body: _ConfirmBody,
    user: dict = Depends(require_roles("customer")),
) -> dict[str, Any]:
    """Đồng ý → chứng từ chốt, đề xuất chốt, hàng đợi đơn vị mở. MỘT transaction.

    Route KHÔNG tự dựng các bước ấy: toàn bộ nằm trong `xac_nhan_de_xuat`, nơi
    có khoá dòng. Một route điều phối ba lời gọi rời nhau sẽ để hở đúng những
    khoảng mà transaction sinh ra để đóng.

    `body` chỉ mang `decision`, và `user["id"]` đến từ JWT. Không trường nào
    trong body ảnh hưởng tới đơn vị nào nhận việc hay giá bao nhiêu.

    Sau lượt này bước vẫn `WAITING_APPROVAL` — chỉ NGƯỜI CHỜ đổi từ khách sang
    đơn vị. Điều đó được suy ra lúc dựng câu trả lời, không ghi ở đâu cả.
    """
    del body  # đã được validate; nội dung duy nhất của nó là "có, tôi đồng ý"
    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        ket_qua = await xac_nhan_de_xuat(pool, proposal_id, owner_user_id=str(user["id"]))
        if not ket_qua.thanh_cong:
            raise HTTPException(status_code=_HTTP[ket_qua.ket_qua], detail=_THONG_DIEP[ket_qua.ket_qua])

        cong_khai = await _cong_khai(pool, ket_qua.de_xuat)
    finally:
        await pool.close()

    # Câu trả lời đang cache được dựng lúc còn chờ KHÁCH. Không bỏ đi thì mọi
    # lượt poll sau vẫn trả "bạn xác nhận giúp mình nhé" dù việc đã sang tay
    # đơn vị — cùng lỗi mà đường duyệt dịch vụ đã phải sửa.
    from src.api.routes import _DEMO_JOBS, request_fresh_answer

    job = _DEMO_JOBS.get(ket_qua.de_xuat.workflow_id)
    if job is not None:
        job["response"] = None
    request_fresh_answer(ket_qua.de_xuat.workflow_id, job=job)

    return {
        **cong_khai,
        # Ai đang chờ, SUY RA chứ không đọc từ cột nào. Trước lượt này là
        # `USER`; sau nó là `PROVIDER`. Không cột nào đổi, và đó là lý do không
        # có chỗ nào để hai câu trả lời lệch nhau.
        "approval_actor": "PROVIDER",
        "waiting_for": "provider",
    }
