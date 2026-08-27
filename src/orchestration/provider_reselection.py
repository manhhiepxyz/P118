"""Đơn vị từ chối → khách CHỦ ĐỘNG yêu cầu tìm đơn vị khác.

Vì sao không tự chuyển
----------------------
Khi đơn vị từ chối, cám dỗ lớn nhất là lặng lẽ hỏi giá lại và đề xuất đơn vị
tiếp theo. Nó sai theo ba cách cùng lúc:

  * Khách không biết lời từ chối đã xảy ra. Họ đồng ý với "Đại Tín, 470.000" và
    một lát sau nhận hoá đơn của một công ty khác với một con số khác.
  * Lý do từ chối chết trong database. "Hết xe ngày ấy" là thông tin có thể đổi
    quyết định của khách — họ có thể muốn đổi ngày thay vì đổi đơn vị.
  * Một chuỗi từ chối liên tiếp biến thành một vòng lặp tự động không ai bấm
    dừng được.

Nên: giữ nguyên mọi bằng chứng, NÓI RA lý do, và chờ một lượt bấm.

Vì sao lần thử MỚI chứ không mở lại lần cũ
------------------------------------------
`service_approvals` của T1 mang `REJECTED`, `reject_code`, lý do và chữ ký
người quyết định. Đó là một sự kiện đã xảy ra. Ghi đè nó để hỏi lại đơn vị khác
là xoá dấu vết một quyết định thật — và lúc có tranh chấp thì không còn gì để
đối chiếu.

T1 chuyển `CANCELLED` (đã bị thay thế), T1R2 ra đời với CÙNG input nghiệp vụ.
Chứng từ và đề xuất mới neo vào T1R2.

Tập loại trừ đến từ DỮ LIỆU
---------------------------
"Đơn vị nào đã từ chối" là một sự kiện có bản ghi. Nó được đọc từ
`service_approvals` của TOÀN BỘ chuỗi lần thử (T1, T1R2, T1R3…), không phải chỉ
lần cuối — nếu không thì sau hai vòng, đơn vị đã từ chối ở vòng một lại được đề
xuất, và khách bấm đúng cái họ vừa bị từ chối.

Không nhận từ client. Một tập loại trừ do client gửi là một tập client sửa
được, và sửa nó nghĩa là chọn hộ đơn vị.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

# `T1` và `T1R2` là hai lần thử của CÙNG một việc. Tách phần gốc để tập loại
# trừ phủ cả chuỗi, không chỉ lần cuối.
_TACH_LAN_THU = "R"


class KetQuaChonLai(StrEnum):
    """Kết quả một lượt "tìm đơn vị khác". Tập ĐÓNG."""

    # Đã mở lần thử mới và có đề xuất chờ khách bấm.
    PROPOSED = "PROPOSED"
    # Không có đề xuất nào, HOẶC nó không thuộc người đang hỏi. Một mã cho hai
    # tình huống: phân biệt chúng là xác nhận với người đang dò rằng một
    # workflow nào đó có thật.
    NOT_FOUND = "NOT_FOUND"
    # Bước này chưa bị từ chối. Không có gì để chọn lại.
    NOT_REJECTED = "NOT_REJECTED"
    # Lần thử mới ĐÃ được mở trước đó — một lượt bấm thứ hai. Không phải lỗi.
    ALREADY_REOPENED = "ALREADY_REOPENED"
    # Hết đơn vị. KHÔNG hồi sinh đơn vị đã từ chối, và không dựng đề xuất giả.
    NO_ALTERNATIVE_PROVIDER = "NO_ALTERNATIVE_PROVIDER"


def goc_lan_thu(task_id: str) -> str:
    """`T1R3` → `T1`. Phần gốc là danh tính LOGIC của một việc.

    Dùng để gom mọi lần thử của cùng một việc lại — tập loại trừ phải phủ cả
    chuỗi, không chỉ lần cuối.
    """
    return task_id.split(_TACH_LAN_THU)[0]


def cung_mot_viec(a: str, b: str) -> bool:
    return goc_lan_thu(a) == goc_lan_thu(b)


@dataclass(frozen=True)
class LoiTuChoi:
    """Lời từ chối mới nhất của một việc, ở dạng khách đọc được."""

    task_id: str
    provider_id: str
    reject_code: str | None
    reason: str | None
    decided_by: str | None
    # Đã có lần thử mới cho việc này chưa. `True` nghĩa là lượt bấm đã xảy ra.
    da_mo_lan_moi: bool = False


@dataclass(frozen=True)
class KetQuaMoLanChonLai:
    ket_qua: KetQuaChonLai
    task_id_moi: str | None = None
    proposal_id: str | None = None
    da_loai: frozenset[str] = field(default_factory=frozenset)


async def don_vi_da_tu_choi(pool: asyncpg.Pool, *, workflow_id: str, task_id: str) -> frozenset[str]:
    """Mọi đơn vị đã từ chối việc này, qua MỌI lần thử.

    Gom theo phần gốc của `task_id`: T1 và T1R2 là hai lần thử của một việc, và
    một đơn vị từ chối ở lần một thì không được đề xuất lại ở lần ba. Chỉ nhìn
    lần cuối nghĩa là sau hai vòng, khách bấm đúng cái họ vừa bị từ chối.

    `EXPIRED` KHÔNG tính là từ chối: hết hạn chờ là hệ thống bỏ cuộc, không
    phải đơn vị nói không. Loại họ vĩnh viễn vì một lượt quá hạn là trừng phạt
    nhầm người.
    """
    goc = goc_lan_thu(task_id)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT task_id, service_provider_id FROM service_approvals "
            "WHERE workflow_id = $1 AND status = 'REJECTED' AND service_provider_id IS NOT NULL",
            UUID(workflow_id),
        )
    return frozenset(r["service_provider_id"] for r in rows if goc_lan_thu(str(r["task_id"])) == goc)


async def loi_tu_choi_dang_cho_khach(pool: asyncpg.Pool, *, workflow_id: str) -> LoiTuChoi | None:
    """Lời từ chối mà khách còn phải quyết định làm gì với nó.

    Trả `None` khi việc ấy đã được mở lần thử mới — lúc đó thứ khách cần thấy
    là ĐỀ XUẤT MỚI, không phải lời từ chối cũ. Để cả hai cùng hiện nghĩa là màn
    hình có hai việc trong khi thật ra chỉ có một.

    Chỉ nhìn dòng có `service_provider_id`: một dòng không chủ là dòng chưa ai
    quyết định được, và nó không thể mang một lời từ chối thật.

    CHỈ dịch vụ có hệ thống báo giá
    -------------------------------
    Phần lớn lời từ chối đã có một đường xử lý TỐT HƠN "tìm đơn vị khác": hệ
    thống hỏi khách một ô cụ thể. Bãi xe hết chỗ Khu A thì câu đúng là "chọn
    Khu B?", không phải "tìm bãi xe khác" — đơn vị vẫn là ban quản lý ấy, và
    thứ đổi được là chỗ đỗ.

    Đo được khi chưa có giới hạn này: bốn bài kiểm của luồng sửa lỗi cũ chuyển
    sang đỏ, vì màn hình bắt đầu mời "tìm đơn vị khác" cho một yêu cầu chỉ cần
    đổi khu. Chọn lại đơn vị chỉ có nghĩa ở nơi ĐƠN VỊ là thứ thay thế được —
    hôm nay là chuyển nhà, nơi có nhiều đối tác cùng làm một việc.
    """
    from src.orchestration.provider_matching import DICH_VU_CO_BAO_GIA

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sa.task_id, sa.service_provider_id, sa.reject_code, sa.reject_reason, sa.decided_by
              FROM service_approvals sa
             WHERE sa.workflow_id = $1 AND sa.status = 'REJECTED'
               AND sa.service_provider_id IS NOT NULL
               AND sa.tool = ANY($2::varchar[])
             ORDER BY sa.decided_at DESC NULLS LAST, sa.task_id DESC
            """,
            UUID(workflow_id),
            sorted(DICH_VU_CO_BAO_GIA),
        )
        moi_buoc = {
            str(r["task_id"])
            for r in await conn.fetch("SELECT task_id FROM workflow_tasks WHERE workflow_id = $1", UUID(workflow_id))
        }
    for row in rows:
        task_id = str(row["task_id"])
        # Đã có lần thử mới cho CÙNG việc này chưa? Nếu có thì lời từ chối này
        # đã được xử lý, và khách đang ở bước tiếp theo.
        da_mo = any(khac != task_id and cung_mot_viec(khac, task_id) and len(khac) > len(task_id) for khac in moi_buoc)
        if da_mo:
            continue
        return LoiTuChoi(
            task_id=task_id,
            provider_id=str(row["service_provider_id"]),
            reject_code=row["reject_code"],
            reason=row["reject_reason"],
            decided_by=row["decided_by"],
        )
    return None


def _lam_sach(cau: str | None) -> str | None:
    """Lý do từ chối, làm sạch để đưa ra màn hình khách.

    Câu này do NGƯỜI của đơn vị gõ, nên nó là văn bản tự do đi thẳng tới một
    người khác. Cắt ký tự điều khiển và gộp khoảng trắng — cùng luật với lúc
    ghi ở `service_approval_routes._sach`, nhưng áp lại ở đường ĐỌC: dữ liệu cũ
    được ghi trước khi có luật ấy vẫn còn trong database.

    KHÔNG lọc nội dung nghiệp vụ. Đơn vị được quyền nói bất cứ điều gì họ cần
    nói, và hệ thống không đọc câu ấy để quyết định gì.
    """
    if not cau:
        return None
    sach = " ".join(str(cau).translate({c: None for c in range(32) if c not in (9, 10, 13)}).split())
    return sach or None


def loi_tu_choi_cong_khai(loi: LoiTuChoi, ten_don_vi: str | None) -> dict[str, Any]:
    """Hình dạng công khai của một lời từ chối. Không payload, không mã lỗi nội bộ."""
    return {
        "rejected_task_id": loi.task_id,
        "rejected_provider": {"id": loi.provider_id, "name": ten_don_vi or loi.provider_id},
        "reject_code": loi.reject_code,
        "sanitized_reason": _lam_sach(loi.reason),
        # Luôn `True` khi lời từ chối còn đang chờ khách: nút "tìm đơn vị khác"
        # là hành động duy nhất ở trạng thái này, và giấu nó đi sẽ để lại một
        # workflow không có đường nào đi tiếp.
        #
        # "Còn đơn vị khác không" KHÔNG được kiểm ở đây: nó cần một vòng hỏi
        # giá, và một lượt ĐỌC không được gọi ra ngoài. Nếu hết đơn vị thì lượt
        # bấm trả `NO_ALTERNATIVE_PROVIDER` và nói ra — một câu trả lời thật,
        # muộn hơn vài giây, tốt hơn một cái nút bị giấu vì một phép đoán.
        "can_request_another_provider": True,
    }


async def mo_lan_chon_lai(
    pool: asyncpg.Pool,
    repository: Any,
    connector: Any,
    *,
    workflow_id: str,
    task_id: str,
    owner_user_id: str,
) -> KetQuaMoLanChonLai:
    """Khách bấm "tìm đơn vị khác" → mở lần thử mới và đề xuất một đơn vị khác.

    Sáu điều kiện, kiểm theo thứ tự từ THÔ tới TINH — và mọi thứ có thể trả về
    bình thường đều nằm TRƯỚC lượt ghi đầu tiên:

      1. workflow tồn tại và thuộc người đang bấm
      2. bước ấy thuộc workflow ấy
      3. bước ấy THẬT SỰ đã bị từ chối
      4. chưa ai mở lần thử mới cho nó (bấm hai lần → `ALREADY_REOPENED`)
      5. còn đơn vị nào chưa từ chối không
      6. đơn vị còn lại có báo giá dùng được không

    Điều kiện 4 là toàn bộ tính bất biến khi bấm đúp: lần thử mới được đặt tên
    theo `task_id` gốc, nên hai lượt song song sẽ cùng nhắm `T1R2` — và cái thứ
    hai phải nhận ra mình đến muộn thay vì mở `T1R3`.

    KHÔNG nhận `provider_id` hay giá từ người gọi. Đơn vị nào được đề xuất là
    kết quả của luật chọn trên tập còn lại, không phải của một tham số.
    """
    from src.db.proposal_repository import de_xuat_dang_cho
    from src.orchestration.proposal_service import de_xuat_don_vi_cho_buoc
    from src.orchestration.quote import van_tay_yeu_cau
    from src.orchestration.quote_service import DICH_VU_CHUYEN_NHA, xin_bao_gia_chuyen_nha
    from src.orchestration.repair_attempt import _allocate_task_id

    try:
        wid = UUID(workflow_id)
        chu = UUID(owner_user_id)
    except (ValueError, AttributeError, TypeError):
        return KetQuaMoLanChonLai(KetQuaChonLai.NOT_FOUND)

    async with pool.acquire() as conn:
        wf = await conn.fetchrow("SELECT owner_user_id FROM workflows WHERE workflow_id = $1", wid)
        # `owner_user_id IS NULL` rơi vào đây: `None != UUID(...)` là `True`.
        if wf is None or wf["owner_user_id"] != chu:
            logger.info("chan yeu cau chon lai ngoai quyen so huu")
            return KetQuaMoLanChonLai(KetQuaChonLai.NOT_FOUND)

        duyet = await conn.fetchrow(
            "SELECT status, service_provider_id FROM service_approvals WHERE workflow_id = $1 AND task_id = $2",
            wid,
            task_id,
        )
        buoc = await conn.fetchrow(
            "SELECT tool, input_data FROM workflow_tasks WHERE workflow_id = $1 AND task_id = $2",
            wid,
            task_id,
        )
        cac_buoc = {
            str(r["task_id"])
            for r in await conn.fetch("SELECT task_id FROM workflow_tasks WHERE workflow_id = $1", wid)
        }

    if buoc is None:
        return KetQuaMoLanChonLai(KetQuaChonLai.NOT_FOUND)
    if duyet is None or duyet["status"] != "REJECTED":
        # Chưa bị từ chối thì không có gì để chọn lại. Mở một lần thử ở đây
        # nghĩa là huỷ một yêu cầu đang chờ đơn vị quyết định.
        return KetQuaMoLanChonLai(KetQuaChonLai.NOT_REJECTED)
    if any(k != task_id and cung_mot_viec(k, task_id) and len(k) > len(task_id) for k in cac_buoc):
        # Lượt bấm thứ hai. KHÔNG mở thêm — trả về mã riêng để tầng trên đọc
        # lại và hiện đề xuất đã có, thay vì dựng một lần thử nữa.
        return KetQuaMoLanChonLai(KetQuaChonLai.ALREADY_REOPENED)

    tool = str(buoc["tool"])
    if tool != DICH_VU_CHUYEN_NHA:
        # Chỉ chuyển nhà có hệ thống báo giá. Dịch vụ khác chưa chọn lại được,
        # và nói `NOT_REJECTED` sẽ nói sai — nên dùng mã "hết đơn vị": đúng về
        # hệ quả, và không hứa một đường không tồn tại.
        return KetQuaMoLanChonLai(KetQuaChonLai.NO_ALTERNATIVE_PROVIDER)

    loai_tru = await don_vi_da_tu_choi(pool, workflow_id=workflow_id, task_id=task_id)
    from src.mock.service_providers import DON_VI_CHUYEN_NHA

    con_lai = {d.provider_id for d in DON_VI_CHUYEN_NHA} - loai_tru
    if not con_lai:
        # Hết đơn vị. KHÔNG hồi sinh đơn vị đã từ chối và KHÔNG dựng đề xuất
        # giả — một đề xuất mà đơn vị đã nói không sẽ bị từ chối lần nữa, và
        # lần này khách mất thêm một vòng chờ.
        return KetQuaMoLanChonLai(KetQuaChonLai.NO_ALTERNATIVE_PROVIDER, da_loai=loai_tru)

    task_moi = _allocate_task_id(task_id, cac_buoc)
    if task_moi is None:
        logger.warning("khong cap duoc danh tinh cho lan thu moi")
        return KetQuaMoLanChonLai(KetQuaChonLai.NO_ALTERNATIVE_PROVIDER, da_loai=loai_tru)

    # ------------------------------------------------------------------
    # TỪ ĐÂY BẮT ĐẦU GHI.
    #
    # `supersede_task_with_new_attempt` là một transaction: T1 sang CANCELLED
    # và T1R2 ra đời cùng nhau. Nửa đầu chạy một mình thì yêu cầu của khách
    # biến mất; nửa sau chạy một mình thì hai bước cùng đòi giữ chỗ.
    #
    # `service_approvals` của T1 KHÔNG bị đụng tới: `REJECTED`, `reject_code`,
    # lý do và chữ ký người quyết định là một sự kiện đã xảy ra.
    # ------------------------------------------------------------------
    import json as _json

    input_cu = buoc["input_data"]
    if isinstance(input_cu, str):
        input_cu = _json.loads(input_cu or "{}")
    input_cu = dict(input_cu or {})

    await repository.supersede_task_with_new_attempt(
        workflow_id,
        old_task_id=task_id,
        new_task={"id": task_moi, "tool": tool, "depends_on": [], "input": input_cu},
    )
    logger.info("mo lan chon lai %s -> %s", task_id, task_moi)

    van_tay = van_tay_yeu_cau(input_cu)
    await xin_bao_gia_chuyen_nha(pool, connector, workflow_id=workflow_id, task_id=task_moi, input_data=input_cu)
    lua_chon, de_xuat = await de_xuat_don_vi_cho_buoc(
        pool,
        workflow_id=workflow_id,
        task_id=task_moi,
        service_type=DICH_VU_CHUYEN_NHA,
        request_fingerprint=van_tay,
        loai_tru=loai_tru,
    )
    if de_xuat is None:
        logger.info("chon lai khong ra de xuat: %s", lua_chon.ket_qua)
        return KetQuaMoLanChonLai(KetQuaChonLai.NO_ALTERNATIVE_PROVIDER, task_id_moi=task_moi, da_loai=loai_tru)

    dang_cho = await de_xuat_dang_cho(pool, workflow_id=workflow_id, task_id=task_moi)
    return KetQuaMoLanChonLai(
        KetQuaChonLai.PROPOSED,
        task_id_moi=task_moi,
        proposal_id=(dang_cho or de_xuat).proposal_id,
        da_loai=loai_tru,
    )
