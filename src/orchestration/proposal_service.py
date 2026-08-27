"""Từ một lựa chọn thành một đề xuất bền vững — và chỉ khi lựa chọn ấy có thật.

Bước C trả về một câu trả lời có kiểu; đúng MỘT trong sáu kiểu ấy là "đã chọn
được". Năm kiểu còn lại là câu hỏi hoặc lời từ chối, và chúng KHÔNG được sinh ra
một đề xuất.

Nghe hiển nhiên, nhưng đây chính là chỗ hiển nhiên hay hỏng: `if ket_qua.bao_gia
is not None` trông đúng và sai — `OVER_BUDGET` cũng mang theo một báo giá (của
đơn vị khách chỉ định, để nói ra nó đắt bao nhiêu). Ghim theo điều kiện ấy nghĩa
là một lời từ chối vì vượt ngân sách trở thành một đề xuất mời khách bấm đồng ý,
với đúng con số vừa bị nói là quá đắt.

Nên điều kiện là `ket_qua.da_chon`, và nó chỉ đúng với `SELECTED`.
"""

from __future__ import annotations

import logging

import asyncpg

from src.db.proposal_repository import ghim_de_xuat
from src.orchestration.proposal import DeXuat
from src.orchestration.provider_selection import LuaChonDonVi, chon_don_vi_cho_buoc

logger = logging.getLogger(__name__)


async def de_xuat_don_vi_cho_buoc(
    pool: asyncpg.Pool,
    *,
    workflow_id: str,
    task_id: str,
    service_type: str,
    request_fingerprint: str,
    ten_don_vi_khach_noi: str | None = None,
    max_price: int | None = None,
    loai_tru: frozenset[str] = frozenset(),
) -> tuple[LuaChonDonVi, DeXuat | None]:
    """Chọn rồi ghim. Trả về CẢ HAI: lựa chọn và đề xuất (nếu có).

    Trả cả hai chứ không chỉ đề xuất, vì năm kết quả còn lại đều cần được nói
    ra cho khách: hỏi lại tên, đưa danh sách ứng viên, báo vượt ngân sách kèm
    giá thật. Trả `None` suông thì tầng trên chỉ biết "không có đề xuất" và
    phải đi hỏi lại lần nữa để biết vì sao.

    Ghim là lượt GHI DUY NHẤT của đường này. Bước C vẫn chỉ đọc; chỗ ranh giới
    đọc/ghi nằm ở đây, một dòng, nhìn thấy được.
    """
    lua_chon = await chon_don_vi_cho_buoc(
        pool,
        workflow_id=workflow_id,
        task_id=task_id,
        service_type=service_type,
        request_fingerprint=request_fingerprint,
        ten_don_vi_khach_noi=ten_don_vi_khach_noi,
        max_price=max_price,
        loai_tru=loai_tru,
    )
    # `da_chon`, KHÔNG phải `bao_gia is not None`. `OVER_BUDGET` cũng mang một
    # báo giá — của đơn vị khách chỉ định, để nói ra nó đắt bao nhiêu — và ghim
    # nó nghĩa là mời khách bấm đồng ý với đúng con số vừa bị nói là quá đắt.
    if not lua_chon.da_chon or lua_chon.bao_gia is None:
        logger.info("không ghim đề xuất, lựa chọn trả về %s", lua_chon.ket_qua)
        return lua_chon, None

    de_xuat = await ghim_de_xuat(pool, workflow_id=workflow_id, task_id=task_id, quote_id=lua_chon.bao_gia.quote_id)
    return lua_chon, de_xuat
