"""MỘT hàm chọn đơn vị, cho cả ba đường vào. Chỉ đọc — chưa cam kết gì.

Ba đường vào, một hàm
---------------------
    khách nói rõ tên       "cho tôi bên Đại Tín"
    khách nói ngân sách    "trong khoảng 450 nghìn"
    khách không nói gì     "đặt giúp tôi chuyển nhà ngày 30/9"

Viết ba nhánh là cách nhanh nhất để chúng lệch nhau — và chỗ lệch sẽ nằm đúng ở
luật phá thế hoà hoặc ở luật ngân sách, tức những chỗ không ai nhìn thấy cho
tới khi hoá đơn sai. Ba đường vào ở đây khác nhau đúng hai tham số, và cùng đi
qua cùng một chuỗi quyết định.

Chỉ chọn từ CHỨNG TỪ ĐÃ PERSIST
-------------------------------
Đầu vào là các báo giá đọc lên từ `service_quotes`. Không tính giá tại chỗ,
không gọi provider, không dùng bảng giá trong mã. Thứ được đề xuất phải là thứ
đối chiếu được — đó là toàn bộ điểm của bước B, và bước này không được phá nó.

KHÔNG BAO GIỜ TỰ ĐỔI Ý CỦA KHÁCH
--------------------------------
Khách chỉ đích danh một đơn vị mà đơn vị ấy vượt ngân sách → nói ra XUNG ĐỘT,
không lặng lẽ chọn bên rẻ hơn. Đơn vị ấy không báo giá → nói ra, không thay
bằng bên khác. Hai điều kiện của khách mâu thuẫn nhau là chuyện của khách; tự
gỡ hộ nghĩa là quyết định thay họ về tiền, và họ chỉ biết khi đọc hoá đơn.

Chỉ ĐỌC
-------
Không xác nhận báo giá, không ghim hàng đợi duyệt, không gọi ra ngoài. Kết quả
là một câu trả lời có kiểu để tầng trên dùng — bước D mới là nơi nó thành một
đề xuất được persist và được khách bấm đồng ý.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import asyncpg

from src.db.quote_repository import bao_gia_dang_song
from src.mock.service_providers import DON_VI_BAO_TRI, DON_VI_CHUYEN_NHA
from src.orchestration.provider_resolver import tra_ten_don_vi
from src.orchestration.quote import BaoGia, loc_theo_ngan_sach

logger = logging.getLogger(__name__)

KetQua = Literal[
    "SELECTED",
    "UNKNOWN_PROVIDER",
    "AMBIGUOUS_PROVIDER",
    "OVER_BUDGET",
    "NO_AVAILABLE_QUOTE",
    "INVALID_BUDGET",
]

# Đánh giá, đọc từ NGUỒN CANONICAL. Chỉ dùng để phá thế hoà khi bằng giá —
# không phải một tiêu chí xếp hạng độc lập, vì nó không nằm trên chứng từ.
_DANH_GIA: dict[str, float] = {d.provider_id: d.danh_gia for d in (*DON_VI_CHUYEN_NHA, *DON_VI_BAO_TRI)}


@dataclass(frozen=True)
class LuaChonDonVi:
    """Câu trả lời có KIỂU. `ket_qua` là hợp đồng, phần còn lại là để nói ra lý do."""

    ket_qua: KetQua
    bao_gia: BaoGia | None = None
    # Khi khách chỉ đích danh: mã đã tra ra được, kể cả khi sau đó nó rớt vì
    # ngân sách hay vì không có báo giá. Tầng trên cần nó để gọi đúng tên đơn vị
    # trong câu trả lời.
    provider_id: str | None = None
    # Khi mơ hồ: các mã cùng khớp, để hỏi lại đúng trọng tâm.
    ung_vien: tuple[str, ...] = ()
    # Khi vượt ngân sách hoặc không có gì vừa: giá thật rẻ nhất đang có. Thiếu
    # nó thì lời từ chối là một khẳng định không kiểm chứng được, và khách
    # không biết mình đang thiếu bao nhiêu.
    gia_re_nhat: int | None = None

    @property
    def da_chon(self) -> bool:
        return self.ket_qua == "SELECTED"


def _xep_hang(bao_gia: BaoGia) -> tuple[int, float, str]:
    """Giá thấp nhất → đánh giá cao hơn → `service_provider_id` nhỏ hơn.

    Vế cuối trông thừa nhưng không thừa: thiếu nó thì hai đơn vị bằng giá bằng
    đánh giá cho kết quả phụ thuộc thứ tự đọc lên từ database, và mọi bài kiểm
    đi qua nó đều nhấp nháy.
    """
    return (bao_gia.amount, -_DANH_GIA.get(bao_gia.service_provider_id, 0.0), bao_gia.service_provider_id)


def chon_don_vi(
    bao_gia: list[BaoGia],
    *,
    service_type: str,
    ten_don_vi_khach_noi: str | None = None,
    max_price: int | None = None,
    loai_tru: frozenset[str] = frozenset(),
) -> LuaChonDonVi:
    """Chọn một báo giá, hoặc nói rõ vì sao không chọn được.

    Thứ tự quyết định, và nó không đảo được:

      1. Lọc chứng từ còn sống. Hết hạn thì không phải một lựa chọn, kể cả khi
         nó rẻ nhất.
      2. Khách có chỉ đích danh không? Có thì TRA TÊN trước mọi thứ khác — một
         cái tên không tra ra được thì ngân sách chưa liên quan gì.
      3. Đơn vị ấy có báo giá không? Không có thì đó là câu trả lời, không phải
         một lý do để chọn bên khác.
      4. Vượt ngân sách? Nói ra xung đột. Không tự gỡ.
      5. Không ai được chỉ định: lọc theo ngân sách rồi xếp hạng tất định.

    `loai_tru` cắt trước cả bước 1: một đơn vị đã từ chối không phải là "lựa
    chọn đắt hơn", nó không còn là lựa chọn.

    Trước cả năm bước: ngân sách phải đọc được, và chứng từ phải đúng dịch vụ.
    Hai hàng rào ấy bắt lỗi LẬP TRÌNH chứ không phải tình huống của khách, nên
    chúng đứng ngoài chuỗi trên.
    """
    # NGÂN SÁCH phải là số nguyên dương, kiểm ở BIÊN.
    #
    # `max_price` đến từ một lượt trích của model, nên nó có thể là `"450000"`,
    # `-1`, hay `True` (`bool` là `int` trong Python — `True <= amount` luôn
    # False, nên nó âm thầm loại sạch mọi báo giá). Cả ba đều sẽ đi lọt qua
    # phép so sánh và ra `OVER_BUDGET` — một câu trả lời SAI về nghiệp vụ cho
    # một lỗi kiểu dữ liệu, và khách được bảo đi nâng ngân sách.
    #
    # Kết quả có kiểu riêng chứ không ném: biên này phải toàn phần, và tầng
    # trên cần phân biệt "ngân sách không đọc được" với "không ai vừa túi".
    if max_price is not None and (isinstance(max_price, bool) or not isinstance(max_price, int) or max_price <= 0):
        # KIỂU, không phải GIÁ TRỊ. Ngân sách là thông tin tài chính riêng của
        # khách; log đi vào file, vào máy chủ log tập trung, vào ảnh chụp màn
        # hình khi gỡ lỗi — những chỗ không có ai kiểm soát ai đọc.
        #
        # Kiểu đã đủ để sửa lỗi: nó nói `str` hay `bool` hay số âm, tức nói
        # đúng thứ người sửa cần. Giá trị không thêm gì cho việc sửa, và thêm
        # một chỗ rò cho việc khác.
        logger.warning("ngân sách không dùng được, kiểu %s", type(max_price).__name__)
        return LuaChonDonVi("INVALID_BUDGET")

    # ĐÚNG DỊCH VỤ, kiểm ở đây chứ không chỉ ở wrapper đọc database.
    #
    # Caller truyền thẳng một danh sách là đường vào hợp lệ (và là đường mọi
    # bài kiểm luật đi qua), nên hàng rào nằm ở wrapper thôi là hàng rào chỉ có
    # với một trong hai đường vào. Một chứng từ bảo trì lọt vào lượt chọn
    # chuyển nhà sẽ được chọn bình thường — cùng hình dạng, khác ngành.
    dung_dich_vu = [q for q in bao_gia if q.service_type == service_type]
    if len(dung_dich_vu) != len(bao_gia):
        # Lỗi lập trình, không phải tình huống của khách: loại rồi báo ĐỎ trong
        # log. Loại chứ không ném vì fail-closed vẫn cho ra một câu trả lời an
        # toàn; ném thì một chứng từ lạc làm hỏng cả lượt phục vụ.
        logger.error(
            "loại %d chứng từ khác dịch vụ khỏi lượt chọn %s",
            len(bao_gia) - len(dung_dich_vu),
            service_type,
        )

    # ĐƠN VỊ ĐÃ TỪ CHỐI bị loại khỏi mọi lượt chọn sau.
    #
    # Tập loại trừ đến từ dữ liệu đã persist (`service_approvals` REJECTED),
    # KHÔNG từ client và không từ model — "đơn vị nào đã từ chối" là một sự
    # kiện có bản ghi, và mọi cách khác để trả lời nó đều là đoán.
    #
    # Loại ở tầng CHỌN chứ không ở tầng hỏi giá: báo giá của họ vẫn được ghim
    # làm bằng chứng (nó trả lời "giá thị trường lúc ấy là bao nhiêu"), chỉ là
    # nó không còn là một lựa chọn. Không hỏi giá họ nữa sẽ làm câu "rẻ nhất
    # đang có" nói về một thị trường đã bị cắt xén.
    con_song = [
        q for q in dung_dich_vu if q.status == "ACTIVE" and not q.het_han and q.service_provider_id not in loai_tru
    ]
    re_nhat = min((q.amount for q in con_song), default=None)

    if ten_don_vi_khach_noi is not None and ten_don_vi_khach_noi.strip():
        tra = tra_ten_don_vi(ten_don_vi_khach_noi, service_type=service_type)
        if tra.trang_thai == "UNKNOWN":
            return LuaChonDonVi("UNKNOWN_PROVIDER", gia_re_nhat=re_nhat)
        if tra.trang_thai == "AMBIGUOUS":
            return LuaChonDonVi("AMBIGUOUS_PROVIDER", ung_vien=tra.ung_vien, gia_re_nhat=re_nhat)

        ma = tra.provider_id
        cua_ho = [q for q in con_song if q.service_provider_id == ma]
        if not cua_ho:
            # Đơn vị có thật nhưng không báo giá cho yêu cầu này: bận ngày ấy,
            # không nhận loại việc ấy, hoặc vừa hết hạn. Đây là CÂU TRẢ LỜI —
            # thay bằng một đơn vị khác là quyết định thay khách.
            return LuaChonDonVi("NO_AVAILABLE_QUOTE", provider_id=ma, gia_re_nhat=re_nhat)

        # Một đơn vị không được có hai báo giá ACTIVE cho cùng một yêu cầu
        # (ràng buộc ở database). Vẫn xếp hạng phòng khi hai vân tay khác nhau
        # cùng lọt vào danh sách — lấy `min` là tất định, lấy phần tử đầu thì
        # phụ thuộc thứ tự đọc.
        chon = min(cua_ho, key=_xep_hang)
        if max_price is not None and chon.amount > max_price:
            return LuaChonDonVi("OVER_BUDGET", provider_id=ma, bao_gia=chon, gia_re_nhat=re_nhat)
        return LuaChonDonVi("SELECTED", bao_gia=chon, provider_id=ma, gia_re_nhat=re_nhat)

    if not con_song:
        return LuaChonDonVi("NO_AVAILABLE_QUOTE")

    trong_ngan_sach = loc_theo_ngan_sach(con_song, max_price)
    if not trong_ngan_sach:
        # Không nới ngân sách hộ, không chọn "gần nhất". Trả kèm giá thật rẻ
        # nhất để khách tự quyết định có nâng ngân sách hay không.
        return LuaChonDonVi("OVER_BUDGET", gia_re_nhat=re_nhat)

    chon = min(trong_ngan_sach, key=_xep_hang)
    return LuaChonDonVi("SELECTED", bao_gia=chon, provider_id=chon.service_provider_id, gia_re_nhat=re_nhat)


async def chon_don_vi_cho_buoc(
    pool: asyncpg.Pool,
    *,
    workflow_id: str,
    task_id: str,
    service_type: str,
    request_fingerprint: str,
    ten_don_vi_khach_noi: str | None = None,
    max_price: int | None = None,
    loai_tru: frozenset[str] = frozenset(),
) -> LuaChonDonVi:
    """Đọc chứng từ của bước rồi chọn. Không ghi gì.

    Lọc theo `request_fingerprint` là bắt buộc, không phải tối ưu: một bước có
    thể còn chứng từ của đời yêu cầu trước nếu lượt dọn chưa chạy, và chọn
    trong đó nghĩa là chọn theo một yêu cầu khách không còn hỏi.
    """
    dang_song = await bao_gia_dang_song(
        pool, workflow_id=workflow_id, task_id=task_id, request_fingerprint=request_fingerprint
    )
    return chon_don_vi(
        dang_song,
        service_type=service_type,
        ten_don_vi_khach_noi=ten_don_vi_khach_noi,
        max_price=max_price,
        loai_tru=loai_tru,
    )
