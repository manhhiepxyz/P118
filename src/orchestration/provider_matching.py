"""Đường chọn đơn vị theo báo giá, nối vào ranh giới thực thi.

Đây là chỗ bốn bước B–D được ghép lại thành MỘT việc: hỏi giá → chọn → đề xuất
→ chờ khách bấm. Nó nằm ở ranh giới thực thi chứ không ở Planner, và khác biệt
ấy là toàn bộ điểm của thiết kế.

Vì sao không nối vào Planner
----------------------------
Model không bao giờ được sinh ra `provider_id`, `quote_id`, giá hay trạng thái
đề xuất. Nếu Planner tạo được chúng thì một lượt sinh sai là một cam kết thương
mại sai, và mọi hàng rào của B/C/D đứng sau một thứ không kiểm chứng được.

Model chỉ trích ra hai mẩu — một đoạn TÊN và một con số NGÂN SÁCH — và cả hai
đi qua `provider_selection` để mã quyết định. Ở đây không có lời gọi model nào.

Vì sao ranh giới thực thi
-------------------------
`ServiceApprovalBoundary._park` là chỗ DUY NHẤT ghim hàng đợi duyệt cho một
bước cần đơn vị. Chen vào đó nghĩa là mọi đường dẫn tới hàng đợi — chạy lần
đầu, chạy tiếp sau khi sửa, resume sau khi duyệt — đều đi qua cùng một luật.
Chen ở một chỗ khác là để lại ít nhất một đường không đi qua.

Thứ tự KHÔNG ĐẢO ĐƯỢC
---------------------
Khi đường này nhận một bước, nó KHÔNG ghim hàng đợi đơn vị. Hàng đợi chỉ mở sau
khi khách bấm đồng ý (`xac_nhan_de_xuat`). Ghim trước nghĩa là đơn vị nhận việc
trước khi khách chọn họ — và lúc khách đổi ý thì bên kia đã bắt đầu xếp lịch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import asyncpg

from src.common.feature_flags import chon_don_vi_theo_bao_gia_bat
from src.db.proposal_repository import de_xuat_dang_cho, doc_de_xuat, trang_thai_hieu_luc
from src.db.quote_repository import doc_bao_gia
from src.orchestration.proposal import DeXuat
from src.orchestration.proposal_service import de_xuat_don_vi_cho_buoc
from src.orchestration.provider_directory import ten_don_vi
from src.orchestration.provider_selection import LuaChonDonVi
from src.orchestration.quote import van_tay_yeu_cau
from src.orchestration.quote_service import DICH_VU_CHUYEN_NHA, xin_bao_gia_chuyen_nha

logger = logging.getLogger(__name__)

# Dịch vụ đã có hệ thống báo giá. CHỈ chuyển nhà trong bước này.
#
# Bảo trì có cùng hình dạng dữ liệu và cùng danh mục đơn vị, nhưng chưa có
# endpoint báo giá ở mock provider — thêm nó vào đây trước khi có endpoint sẽ
# làm mọi yêu cầu bảo trì rơi vào nhánh "không đơn vị nào báo giá", tức hỏng
# một luồng đang chạy để mở một luồng chưa chạy.
DICH_VU_CO_BAO_GIA: frozenset[str] = frozenset({DICH_VU_CHUYEN_NHA})


@dataclass(frozen=True)
class TuyChonChonDonVi:
    """Sở thích chọn đơn vị của khách. KHÔNG phải input nghiệp vụ của dịch vụ.

    Đây là ranh giới quan trọng nhất của file này, và nó dễ bị xoá nhầm.

    `schedule_move` có đúng năm input theo hợp đồng: ngày, giờ, xe, thang máy,
    bốc xếp. Đó là những gì ĐƠN VỊ cần để làm việc. "Cho tôi bên Đại Tín" và
    "trong khoảng 450 nghìn" thì không — chúng là cách P-118 chọn giúp khách,
    và đơn vị không bao giờ được nhìn thấy chúng:

      * ngân sách rời khỏi P-118 nghĩa là đơn vị định giá theo túi tiền người
        hỏi thay vì theo công việc — đúng điều bước B dựng hai hàng rào để chặn;
      * tên đơn vị khách nói là một mẩu chưa xác minh, và nó chỉ có nghĩa với
        resolver, không có nghĩa với bất kỳ ai bên ngoài.

    Nên chúng KHÔNG được nới vào schema `schedule_move`, KHÔNG được ghi vào
    `task.input_data`, và KHÔNG có mặt trong payload gửi provider. Nới schema
    là mở một đường cho Planner ghi chúng vào bước, và từ đó chúng đi theo bước
    tới mọi nơi bước đi — kể cả ra ngoài.

    Hôm nay KHÔNG có nguồn nào cấp hai giá trị này, và mặc định là `None`. Đó
    là trạng thái đúng, không phải một chỗ chưa làm xong: nhánh "khách không
    nói gì → chọn đơn vị hợp lý nhất" đã chạy đầy đủ qua đường thật. Nhận sở
    thích bằng ngôn ngữ tự nhiên là một hợp đồng RIÊNG — nó cần một chỗ lưu
    riêng (không phải `task.input_data`), một luật vòng đời riêng khi khách đổi
    ý, và một lượt canary trước khi nối. Thiết kế nó sau canary này.
    """

    ten_don_vi: str | None = None
    max_price: int | None = None

    @property
    def ngan_sach_dung_duoc(self) -> int | None:
        """Ngân sách ở dạng `chon_don_vi` nhận được, hoặc `None`.

        `bool` là `int` trong Python, nên `True` sẽ lọt qua một phép kiểm kiểu
        ngây thơ và thành "ngân sách 1 đồng" — mọi báo giá đều vượt. Hàng rào
        thật nằm ở `chon_don_vi` (trả `INVALID_BUDGET`); đây chỉ là chỗ không
        biến một giá trị rác thành một con số trông hợp lệ trên đường đi.
        """
        if isinstance(self.max_price, bool) or not isinstance(self.max_price, int):
            return None
        return self.max_price


KHONG_CO_TUY_CHON = TuyChonChonDonVi()


@dataclass(frozen=True)
class KetQuaGhepDonVi:
    """Kết quả một lượt chuẩn bị đề xuất cho MỘT bước."""

    lua_chon: LuaChonDonVi
    de_xuat: DeXuat | None
    # Đề xuất này đã có từ trước và được DÙNG LẠI, không phải vừa tạo. Phân
    # biệt để tầng trên biết một lượt poll không sinh thêm gì.
    dung_lai: bool = False
    # Bước này khách ĐÃ đồng ý rồi — việc đang nằm ở hàng đợi của đơn vị.
    # Không đề xuất gì thêm, và cũng không đụng vào dòng duyệt đã có.
    da_xac_nhan: bool = False

    @property
    def cho_khach_xac_nhan(self) -> bool:
        return self.de_xuat is not None


def dich_vu_di_qua_bao_gia(tool: str) -> bool:
    """Bước này có đi qua đường báo giá không.

    Hai điều kiện, và cờ đứng TRƯỚC: khi cờ tắt thì không có câu hỏi nào về
    dịch vụ nữa, và đường cũ chạy nguyên vẹn.
    """
    return chon_don_vi_theo_bao_gia_bat() and tool in DICH_VU_CO_BAO_GIA


async def _de_xuat_con_dung_duoc(pool: asyncpg.Pool, *, workflow_id: str, task_id: str, van_tay: str) -> DeXuat | None:
    """Đề xuất đang chờ có còn dùng cho ĐÚNG yêu cầu này không.

    Ba điều kiện, và điều thứ ba là điều dễ quên nhất: chứng từ phải mang đúng
    VÂN TAY của yêu cầu hiện tại. Thiếu nó thì một lượt sửa (đổi ngày, đổi xe)
    sẽ dùng lại đề xuất cũ — khách xác nhận một cái giá cho một việc họ không
    còn hỏi.

    Đây là điều kiện của tính bất biến khi poll: cùng một yêu cầu được hỏi lại
    nhiều lần thì không sinh thêm chứng từ và không sinh thêm đề xuất.
    """
    dang_cho = await de_xuat_dang_cho(pool, workflow_id=workflow_id, task_id=task_id)
    if dang_cho is None:
        return None
    _, con_bam_duoc = await trang_thai_hieu_luc(pool, dang_cho)
    if not con_bam_duoc:
        return None
    bao_gia = await doc_bao_gia(pool, dang_cho.quote_id)
    if bao_gia is None or bao_gia.request_fingerprint != van_tay:
        return None
    return dang_cho


async def _da_xac_nhan_cho_yeu_cau_nay(pool: asyncpg.Pool, *, workflow_id: str, task_id: str, van_tay: str) -> bool:
    """Bước này đã có một đề xuất ĐƯỢC XÁC NHẬN cho đúng yêu cầu hiện tại chưa.

    Sau khi khách bấm đồng ý, việc đã sang hàng đợi của đơn vị và bước không
    còn gì để đề xuất. Nhưng nó vẫn nằm `WAITING_APPROVAL`, nên mọi lượt
    `/continue` sau đó vẫn đi qua cổng dịch vụ — và nếu chỗ này chỉ hỏi "còn
    cái nào ĐANG CHỜ không" thì câu trả lời là "không", và nó dựng một đề xuất
    mới. Đo được: hai đề xuất sau một lượt xác nhận, và cái thứ hai mời khách
    chọn lại một việc họ vừa chốt.

    Vân tay phải khớp. Nếu khách đã đổi yêu cầu thì xác nhận cũ không còn nói
    gì về yêu cầu mới, và bước phải đi hỏi giá lại.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1
              FROM service_provider_proposals p
              JOIN service_quotes q ON q.quote_id = p.quote_id
             WHERE p.workflow_id = $1 AND p.task_id = $2
               AND p.status = 'CONFIRMED'
               AND q.request_fingerprint = $3
             LIMIT 1
            """,
            UUID(workflow_id),
            task_id,
            van_tay,
        )
    return row is not None


async def chuan_bi_de_xuat(
    pool: asyncpg.Pool,
    connector: Any,
    *,
    workflow_id: str,
    task_id: str,
    input_data: dict[str, Any],
    tuy_chon: TuyChonChonDonVi = KHONG_CO_TUY_CHON,
) -> KetQuaGhepDonVi:
    """Chuẩn bị một đề xuất cho bước này, hoặc nói rõ vì sao không có.

    Đường đi:

      0. khách ĐÃ đồng ý cho đúng yêu cầu này → không đề xuất gì nữa; việc đang
         nằm ở hàng đợi của đơn vị
      1. đã có đề xuất còn dùng được cho ĐÚNG yêu cầu này → dùng lại, không hỏi
         giá thêm lần nào
      2. chưa có → hỏi giá tất cả đơn vị, chọn bằng luật tất định, ghim đề xuất
      3. không chọn được → KHÔNG ghim gì, và trả về lý do để tầng trên nói ra

    Bước 1 là điều kiện của tính bất biến khi poll. Thiếu nó thì mỗi lượt
    `/continue` là một vòng hỏi giá mới — ba lời gọi HTTP, ba chứng từ mới, một
    đề xuất mới đẩy cái cũ sang SUPERSEDED — và cái khách vừa nhìn thấy trên
    màn hình đã không còn xác nhận được nữa.
    """
    van_tay = van_tay_yeu_cau(input_data)

    if await _da_xac_nhan_cho_yeu_cau_nay(pool, workflow_id=workflow_id, task_id=task_id, van_tay=van_tay):
        logger.info("bước %s đã được xác nhận, không đề xuất thêm", task_id)
        return KetQuaGhepDonVi(lua_chon=LuaChonDonVi("SELECTED"), de_xuat=None, da_xac_nhan=True)

    con_dung = await _de_xuat_con_dung_duoc(pool, workflow_id=workflow_id, task_id=task_id, van_tay=van_tay)
    if con_dung is not None:
        logger.info("dùng lại đề xuất đang chờ cho bước %s", task_id)
        bao_gia = await doc_bao_gia(pool, con_dung.quote_id)
        return KetQuaGhepDonVi(
            lua_chon=LuaChonDonVi("SELECTED", bao_gia=bao_gia, provider_id=bao_gia.service_provider_id),
            de_xuat=con_dung,
            dung_lai=True,
        )

    # Hỏi giá dọn luôn chứng từ đời cũ và chứng từ quá hạn, cùng với đề xuất
    # đang trỏ vào chúng — một transaction, xem `don_bao_gia_va_de_xuat`.
    #
    # `input_data` đi vào đây để tính vân tay và dựng payload gửi đơn vị; sở
    # thích đi bằng một tham số RIÊNG. Trộn chúng vào một dict là cách nhanh
    # nhất để một ngày nào đó `max_price` theo `input_data` ra tới connector.
    await xin_bao_gia_chuyen_nha(
        pool,
        connector,
        workflow_id=workflow_id,
        task_id=task_id,
        input_data=input_data,
        max_price=tuy_chon.ngan_sach_dung_duoc,
    )
    lua_chon, de_xuat = await de_xuat_don_vi_cho_buoc(
        pool,
        workflow_id=workflow_id,
        task_id=task_id,
        service_type=DICH_VU_CHUYEN_NHA,
        request_fingerprint=van_tay,
        ten_don_vi_khach_noi=tuy_chon.ten_don_vi,
        max_price=tuy_chon.max_price,
    )
    return KetQuaGhepDonVi(lua_chon=lua_chon, de_xuat=de_xuat)


async def payload_cho_nguoi_dung(pool: asyncpg.Pool, de_xuat: DeXuat) -> dict[str, Any]:
    """Dữ kiện khách cần để bấm đồng ý. GHÉP lúc đọc, không có bản sao nào.

    Đơn vị và giá đến từ chứng từ; tên đọc được đến từ danh mục canonical;
    trạng thái hiệu lực tính từ cả hai phía. Không trường nào ở đây được lưu
    vào bảng đề xuất — một bản sao là một bản sẽ lệch, và nó lệch đúng vào lúc
    chứng từ hết hạn còn con số cũ trông vẫn hợp lệ.

    `reason` là câu giải thích VÌ SAO đơn vị này được chọn. Nó dựng từ dữ kiện
    đã persist, không phải từ một lượt gọi model: một câu do model viết ra có
    thể nói sai lý do cho đúng con số, và khách không có cách nào biết.
    """
    moi = await doc_de_xuat(pool, de_xuat.proposal_id) or de_xuat
    bao_gia = await doc_bao_gia(pool, moi.quote_id)
    hieu_luc, con_bam_duoc = await trang_thai_hieu_luc(pool, moi)
    if bao_gia is None:  # pragma: no cover - khoá ngoại không cho chứng từ biến mất
        return {"proposal_id": moi.proposal_id, "effective_status": hieu_luc, "can_confirm": False}
    return {
        "proposal_id": moi.proposal_id,
        "provider": {"id": bao_gia.service_provider_id, "name": ten_don_vi(bao_gia.service_provider_id)},
        "amount": bao_gia.amount,
        "currency": bao_gia.currency,
        "reason": _ly_do(bao_gia.amount),
        "valid_until": bao_gia.valid_until.isoformat(),
        "effective_status": hieu_luc,
        "can_confirm": con_bam_duoc,
    }


def _ly_do(so_tien: int) -> str:
    """Câu giải thích, dựng từ dữ kiện — không gọi model, không ghép goal.

    Định dạng số TRƯỚC rồi mới ghép vào câu. Bản đầu ghép trước rồi
    `.replace(",", ".")` trên cả câu — và nó nuốt luôn dấu phẩy của tiếng Việt:

        "Đơn vị phù hợp nhất với yêu cầu của bạn. báo giá 420.000 VND."

    Đo được trên canary. Một chỗ thay thế mù trên cả câu sẽ luôn tìm thấy nhiều
    hơn thứ nó định tìm.
    """
    so = f"{so_tien:,.0f}".replace(",", ".")
    return f"Đơn vị phù hợp nhất với yêu cầu của bạn, báo giá {so} VND."
