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
from src.config import get_settings
from src.db.proposal_repository import doc_de_xuat, trang_thai_hieu_luc, xac_nhan_de_xuat
from src.db.quote_repository import doc_bao_gia
from src.orchestration.proposal import KetQuaXacNhan
from src.orchestration.provider_directory import ten_don_vi
from src.orchestration.provider_reselection import KetQuaChonLai, mo_lan_chon_lai
from src.orchestration.runtime_provider import acquire_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/service-proposals", tags=["service-proposals"])

# Mã kết quả → mã HTTP, và → câu chữ. Hai bảng, và cả hai phải PHỦ HẾT.
#
# Bản đầu để `_HTTP[ket_qua]` nổ `KeyError` khi thêm một kết quả mới, với lý do
# "vỡ to hơn im lặng". Nó vỡ đúng chỗ sai: ở request đầu tiên chạm vào nhánh
# mới, trên máy chủ thật, cho một khách thật. `KetQuaXacNhan` là enum liệt kê
# được, nên bài kiểm parity đối chiếu `set(_HTTP) == set(KetQuaXacNhan)` và
# thiếu một mã sẽ đỏ TRƯỚC khi phát hành.
#
# Runtime vẫn phải chịu được điều không nên xảy ra: `.get()` với một mặc định
# an toàn (500 + câu chữ chung), vì một 500 có log tốt hơn một stack trace lọt
# ra ngoài.
_HTTP: dict[KetQuaXacNhan, int] = {
    KetQuaXacNhan.CONFIRMED: 200,
    # 404 dùng chung cho "không có" và "không phải của bạn" — phân biệt chúng
    # là xác nhận với người đang dò rằng một mã nào đó có thật.
    KetQuaXacNhan.NOT_FOUND: 404,
    KetQuaXacNhan.ALREADY_DECIDED: 409,
    KetQuaXacNhan.QUOTE_EXPIRED: 409,
    KetQuaXacNhan.QUOTE_NOT_USABLE: 409,
}

_THONG_DIEP: dict[KetQuaXacNhan, str] = {
    KetQuaXacNhan.CONFIRMED: "Đã xác nhận.",
    KetQuaXacNhan.NOT_FOUND: "Không tìm thấy đề xuất này.",
    KetQuaXacNhan.ALREADY_DECIDED: "Đề xuất này đã được xử lý trước đó.",
    KetQuaXacNhan.QUOTE_EXPIRED: "Báo giá đã hết hiệu lực. Bạn để mình hỏi lại giá mới nhé.",
    KetQuaXacNhan.QUOTE_NOT_USABLE: "Báo giá này không còn dùng được.",
}

# Câu chữ cho một mã không có trong bảng. Không nhắc tới mã ấy: người đọc là
# khách, và một mã lạ trên màn hình của họ chỉ là tiếng ồn.
_KHONG_XU_LY_DUOC = "Mình chưa xử lý được yêu cầu này. Bạn thử lại sau giúp mình nhé."

_KHONG_TIM_THAY = _THONG_DIEP[KetQuaXacNhan.NOT_FOUND]


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

    `effective_status` và `can_confirm` tính từ CẢ HAI phía. `status` một mình
    là fail-OPEN: chứng từ có thể vừa hết hạn trong khi lượt dọn chưa chạy tới,
    và lúc ấy cột vẫn ghi `PROPOSED` còn màn hình vẫn dựng nút "đồng ý" cho một
    cái giá không còn tồn tại.
    """
    bao_gia = await doc_bao_gia(pool, de_xuat.quote_id)
    hieu_luc, con_bam_duoc = await trang_thai_hieu_luc(pool, de_xuat)
    return {
        "proposal_id": de_xuat.proposal_id,
        "workflow_id": de_xuat.workflow_id,
        "task_id": de_xuat.task_id,
        # `status` là thứ đang nằm trong cột; `effective_status` là sự thật sau
        # khi hỏi cả chứng từ. Trả cả hai để màn hình dùng cái thứ hai mà người
        # gỡ lỗi vẫn thấy được cái thứ nhất.
        "status": de_xuat.status,
        "effective_status": hieu_luc,
        "can_confirm": con_bam_duoc,
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
            raise HTTPException(status_code=404, detail=_KHONG_TIM_THAY)
        chu = await pool.fetchval(
            "SELECT owner_user_id FROM workflows WHERE workflow_id = $1::uuid", de_xuat.workflow_id
        )
        if chu is None or str(chu) != str(user["id"]):
            logger.info("chặn đọc đề xuất ngoài quyền sở hữu")
            raise HTTPException(status_code=404, detail=_KHONG_TIM_THAY)
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
            raise HTTPException(
                status_code=_HTTP.get(ket_qua.ket_qua, 500),
                detail=_THONG_DIEP.get(ket_qua.ket_qua, _KHONG_XU_LY_DUOC),
            )

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


# Mã kết quả chọn lại → mã HTTP và câu chữ. Hai bảng PHỦ HẾT tập kết quả, và có
# bài kiểm parity — cùng khuôn với bảng của lượt xác nhận, và cùng lý do: một
# `KeyError` ở đây sẽ nổ ở request đầu tiên chạm vào nhánh mới, trên máy chủ
# thật, cho một khách thật.
_HTTP_CHON_LAI = {
    KetQuaChonLai.PROPOSED: 200,
    KetQuaChonLai.ALREADY_REOPENED: 200,
    KetQuaChonLai.NOT_FOUND: 404,
    KetQuaChonLai.NOT_REJECTED: 409,
    KetQuaChonLai.NO_ALTERNATIVE_PROVIDER: 409,
}

_THONG_DIEP_CHON_LAI = {
    KetQuaChonLai.PROPOSED: "Mình đã tìm được đơn vị khác cho bạn.",
    # 200, không phải lỗi: lượt bấm thứ hai không làm gì thêm, và thứ khách cần
    # thấy là đề xuất đã có.
    KetQuaChonLai.ALREADY_REOPENED: "Mình đang tìm đơn vị khác cho bạn rồi.",
    KetQuaChonLai.NOT_FOUND: "Không tìm thấy yêu cầu này.",
    KetQuaChonLai.NOT_REJECTED: "Yêu cầu này chưa bị đơn vị nào từ chối.",
    KetQuaChonLai.NO_ALTERNATIVE_PROVIDER: (
        "Hiện không còn đơn vị nào khác nhận được yêu cầu này. "
        "Bạn thử đổi ngày hoặc liên hệ bộ phận hỗ trợ giúp mình nhé."
    ),
}


class _ChonLaiBody(BaseModel):
    """Đúng MỘT trường: bước nào đang cần đơn vị khác.

    KHÔNG nhận `provider_id`, KHÔNG nhận giá. Đơn vị nào được đề xuất là kết
    quả của luật chọn trên tập còn lại, không phải của một tham số — và một
    tham số nhận được thì sớm muộn sẽ có người tin nó.

    `task_id` là định danh khách ĐÃ NHÌN THẤY trong response (`rejected_task_id`),
    và server kiểm lại rằng nó thuộc workflow này và thật sự đã bị từ chối. Suy
    nó ra ở server nghe an toàn hơn nhưng sai khi một workflow có hai bước bị
    từ chối — lúc ấy server phải đoán khách đang nói về bước nào.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=20)


@router.post(
    "/workflows/{workflow_id}/request-another-provider",
    summary="Khách yêu cầu tìm đơn vị khác sau khi bị từ chối",
)
async def request_another_provider(
    workflow_id: str,
    body: _ChonLaiBody,
    user: dict = Depends(require_roles("customer")),
) -> dict[str, Any]:
    """Mở một lần thử MỚI cho bước vừa bị từ chối, với một đơn vị khác.

    Tên endpoint nói đúng việc nó làm. Gọi nó là "confirm lần hai" sẽ che mất
    hai điều: đây là một lần thử KHÁC (bằng chứng cũ giữ nguyên), và đơn vị lần
    này là một đơn vị khác.

    CHỈ `customer`. Đơn vị và admin không bấm hộ: chính khách là người phải
    quyết định giữa "tìm đơn vị khác" và "đổi ngày rồi hỏi lại chính đơn vị
    này" — và lý do từ chối là thứ giúp họ chọn.

    Bấm hai lần trả 200 với `ALREADY_REOPENED`, không phải 409: lượt thứ hai
    không làm gì thêm, và thứ khách cần thấy là đề xuất đã có. Trả lỗi ở đây
    biến một cú bấm đúp thành một thông báo đỏ cho một việc đã thành công.
    """
    from src.connectors.resident_services import ResidentServicesConnector

    repository = await acquire_repository()
    pool = repository._pool  # noqa: SLF001 - composition root sở hữu pool
    try:
        ket_qua = await mo_lan_chon_lai(
            pool,
            repository,
            ResidentServicesConnector(base_url=get_settings().resident_services_service_url),
            workflow_id=workflow_id,
            task_id=body.task_id,
            owner_user_id=str(user["id"]),
        )
    finally:
        await pool.close()

    ma = _HTTP_CHON_LAI.get(ket_qua.ket_qua, 500)
    if ma >= 400:
        raise HTTPException(status_code=ma, detail=_THONG_DIEP_CHON_LAI.get(ket_qua.ket_qua, _KHONG_XU_LY_DUOC))

    # Câu trả lời đang cache được dựng lúc còn hiện lời từ chối. Không bỏ đi thì
    # mọi lượt poll sau vẫn nói "đơn vị đã từ chối" dù đã có đề xuất mới.
    from src.api.routes import _DEMO_JOBS, request_fresh_answer

    job = _DEMO_JOBS.get(workflow_id)
    if job is not None:
        job["response"] = None
    request_fresh_answer(workflow_id, job=job)

    return {
        "workflow_id": workflow_id,
        "outcome": str(ket_qua.ket_qua),
        "new_task_id": ket_qua.task_id_moi,
        "proposal_id": ket_qua.proposal_id,
        "message": _THONG_DIEP_CHON_LAI[ket_qua.ket_qua],
        # Người chờ đổi lại về KHÁCH: có một đề xuất mới cần họ bấm.
        "approval_actor": "USER",
    }
