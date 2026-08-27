"""Đi hỏi giá, ghim lại, rồi mới lọc theo ngân sách — theo đúng thứ tự ấy.

Thứ tự là phần quan trọng nhất của file này:

    yêu cầu canonical
      → hỏi TẤT CẢ đơn vị đủ điều kiện, KHÔNG gửi max_price
      → đơn vị trả báo giá
      → P-118 persist TỪNG báo giá
      → P-118 lọc theo max_price
      → luật tất định chọn đề xuất

Đảo hai bước cuối lên trước là hỏng cả cơ chế. Nếu gửi ngân sách đi thì đơn vị
trả về một con số sát ngân sách, và "đơn vị rẻ nhất" đo một thứ do chính P-118
tạo ra. Nếu lọc trước khi persist thì các báo giá bị loại không để lại dấu vết
— và lúc khách hỏi "sao không có ai trong 500k" thì không có gì để trả lời
ngoài một lần chạy lại.

Persist TRƯỚC khi lọc còn là điều kiện để câu trả lời "không ai vừa ngân sách"
trung thực: nó nói được giá thật rẻ nhất là bao nhiêu, vì con số ấy đang nằm
trong database chứ không phải trong một biến vừa bị vứt đi.

Xin báo giá KHÔNG tạo side effect. Không đặt chỗ, không giữ lịch, không ghim
hàng đợi duyệt. Nên khi không ai vừa ngân sách, không có gì phải hoàn tác —
điều đó phải đúng theo THIẾT KẾ, không phải nhờ cẩn thận.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import asyncpg

from src.db.quote_repository import bao_gia_dang_song, luu_bao_gia, thay_the_bao_gia_cu
from src.mock.service_providers import DON_VI_CHUYEN_NHA
from src.orchestration.quote import (
    BaoGia,
    loc_theo_ngan_sach,
    payload_gui_provider,
    van_tay_yeu_cau,
)

logger = logging.getLogger(__name__)

DICH_VU_CHUYEN_NHA = "schedule_move"


class NguonBaoGia(Protocol):
    """Bề mặt tối thiểu mà tầng này cần từ connector.

    Protocol chứ không phải class cụ thể: bài kiểm luật không cần một tiến
    trình HTTP, và một tham số kiểu `ResidentServicesConnector` buộc mọi bài
    kiểm phải dựng một cái.
    """

    async def xin_bao_gia_chuyen_nha(self, service_provider_id: str, payload: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class KetQuaBaoGia:
    """Kết quả một lượt đi hỏi giá.

    `tat_ca` là MỌI báo giá đã persist, `trong_ngan_sach` là phần vừa túi, và
    `de_xuat` là cái được chọn. Ba thứ khác nhau, và trả cả ba là cố ý: câu trả
    lời "không ai trong 500k, rẻ nhất là 620k" cần cả `tat_ca` lẫn
    `trong_ngan_sach` mới nói được.
    """

    tat_ca: list[BaoGia]
    trong_ngan_sach: list[BaoGia]
    de_xuat: BaoGia | None
    tu_choi: dict[str, str]  # provider_id → mã lý do

    @property
    def gia_re_nhat(self) -> int | None:
        return min((q.amount for q in self.tat_ca), default=None)


def chon_bao_gia(ung_vien: list[BaoGia]) -> BaoGia | None:
    """Giá thấp nhất → đánh giá cao hơn → `service_provider_id` nhỏ hơn.

    Cùng luật với `mock.service_providers.chon_don_vi`, nhưng chạy trên BÁO GIÁ
    ĐÃ PERSIST chứ không trên bảng giá trong mã. Đó là toàn bộ điểm của bước B:
    thứ được chọn phải là thứ có chứng từ.

    Vế cuối trông thừa nhưng không thừa — thiếu nó thì hai đơn vị bằng giá bằng
    đánh giá cho kết quả phụ thuộc thứ tự trong danh sách, và mọi bài kiểm đi
    qua nó đều nhấp nháy.
    """
    if not ung_vien:
        return None
    danh_gia = {d.provider_id: d.danh_gia for d in DON_VI_CHUYEN_NHA}
    return min(
        ung_vien,
        key=lambda q: (q.amount, -danh_gia.get(q.service_provider_id, 0.0), q.service_provider_id),
    )


def _valid_until(gia_tri: Any) -> datetime | None:
    """Hạn hiệu lực do provider trả về. Không đọc được thì KHÔNG đoán.

    Một báo giá không nói rõ mình sống tới bao giờ là một báo giá không dùng
    được. Gán một mặc định hộ nghĩa là P-118 tự hứa thay đơn vị — và lời hứa ấy
    sẽ được đem ra thu tiền.
    """
    if isinstance(gia_tri, datetime):
        return gia_tri
    try:
        return datetime.fromisoformat(str(gia_tri))
    except (TypeError, ValueError):
        return None


async def xin_bao_gia_chuyen_nha(
    pool: asyncpg.Pool,
    connector: NguonBaoGia,
    *,
    workflow_id: str,
    task_id: str,
    input_data: dict[str, Any],
    max_price: int | None = None,
) -> KetQuaBaoGia:
    """Hỏi mọi đơn vị, ghim từng câu trả lời, rồi mới lọc.

    Hỏi SONG SONG: ba lượt tuần tự là ba lần độ trễ mạng cộng dồn cho một việc
    không có thứ tự nào. `return_exceptions=True` để một đơn vị sập không kéo
    theo cả lượt — mất một lựa chọn còn hơn mất cả bảng giá.

    Báo giá của vân tay CŨ thành SUPERSEDED trước khi ghim cái mới: nếu khách
    vừa đổi ngày thì mọi thứ đơn vị đã hứa cho ngày cũ không còn là lời hứa cho
    ngày này. Không dọn thì `bao_gia_dang_song` trả về cả hai đời, và luật chọn
    sẽ lấy cái rẻ hơn — tức chọn theo một yêu cầu khách không còn hỏi.
    """
    van_tay = van_tay_yeu_cau(input_data)
    payload = payload_gui_provider(input_data)

    da_thay_the = await thay_the_bao_gia_cu(pool, workflow_id=workflow_id, task_id=task_id, van_tay_moi=van_tay)
    if da_thay_the:
        logger.info("yêu cầu đã đổi, %d báo giá cũ chuyển SUPERSEDED", da_thay_the)

    ma_don_vi = [d.provider_id for d in DON_VI_CHUYEN_NHA]
    phan_hoi = await asyncio.gather(
        *(connector.xin_bao_gia_chuyen_nha(ma, dict(payload)) for ma in ma_don_vi),
        return_exceptions=True,
    )

    tu_choi: dict[str, str] = {}
    for ma, ket_qua in zip(ma_don_vi, phan_hoi, strict=True):
        if isinstance(ket_qua, BaseException):
            # Đơn vị sập KHÔNG thành một báo giá. Không có báo giá thì không có
            # đề xuất — chứ không phải một đề xuất với con số đoán ra.
            logger.warning("đơn vị %s lỗi khi báo giá: %s", ma, ket_qua)
            tu_choi[ma] = "PROVIDER_ERROR"
            continue
        if not getattr(ket_qua, "success", False):
            tu_choi[ma] = str(getattr(ket_qua, "error_code", None) or "PROVIDER_ERROR")
            continue

        data = getattr(ket_qua, "data", None) or {}
        han = _valid_until(data.get("valid_until"))
        # Đơn vị trả về CHÍNH NÓ, không phải một đơn vị khác. Tin `data` một
        # cách mù quáng nghĩa là một provider bị chiếm quyền báo giá hộ hàng
        # xóm — và chữ ký trên chứng từ sẽ mang tên người không báo.
        if han is None or data.get("service_provider_id") != ma:
            logger.warning("đơn vị %s trả báo giá sai hợp đồng", ma)
            tu_choi[ma] = "QUOTE_MALFORMED"
            continue
        try:
            await luu_bao_gia(
                pool,
                external_quote_id=str(data["external_quote_id"]),
                service_provider_id=ma,
                service_type=DICH_VU_CHUYEN_NHA,
                amount=int(data["amount"]),
                currency=str(data["currency"]),
                request_fingerprint=van_tay,
                valid_until=han,
                workflow_id=workflow_id,
                task_id=task_id,
            )
        except asyncpg.UniqueViolationError:
            # Đơn vị này ĐÃ báo giá cho đúng yêu cầu này. Không phải lỗi: một
            # lượt hỏi lại (retry sau timeout, hai tab, người dùng bấm lại)
            # phải dừng ở đây thay vì để lại dòng ACTIVE thứ hai. Ghi một mã
            # RIÊNG chứ không gộp vào `QUOTE_MALFORMED`: gộp lại thì lúc đọc
            # log không phân biệt được "provider trả rác" với "ta hỏi hai lần".
            logger.info("đơn vị %s đã có báo giá ACTIVE cho yêu cầu này", ma)
            tu_choi[ma] = "QUOTE_ALREADY_ISSUED"
        except (ValueError, KeyError, TypeError, asyncpg.PostgresError) as exc:
            # Sai schema thì KHÔNG persist, và KHÔNG có đề xuất giả. Một dòng
            # bị bỏ ở đây tệ hơn hẳn một dòng sai được ghi: dòng sai sẽ được
            # đem ra thu tiền.
            logger.warning("báo giá của %s không ghi được: %s", ma, exc)
            tu_choi[ma] = "QUOTE_MALFORMED"

    # Đọc LẠI từ database, không dùng danh sách vừa dựng trong RAM. Thứ được
    # đem ra chọn phải là thứ đã persist — nếu một dòng không ghi được, nó
    # không được xuất hiện trong đề xuất chỉ vì biến vẫn còn giữ nó.
    tat_ca = await bao_gia_dang_song(pool, workflow_id=workflow_id, task_id=task_id, request_fingerprint=van_tay)
    trong_ngan_sach = loc_theo_ngan_sach(tat_ca, max_price)
    return KetQuaBaoGia(
        tat_ca=tat_ca,
        trong_ngan_sach=trong_ngan_sach,
        de_xuat=chon_bao_gia(trong_ngan_sach),
        tu_choi=tu_choi,
    )
