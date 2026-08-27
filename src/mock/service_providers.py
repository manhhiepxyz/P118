"""Danh mục đơn vị cung cấp cho chuyển nhà và bảo trì, kèm bảng giá theo tham số.

Vì sao có module này
--------------------
Trước đây mỗi tool nối cứng MỘT provider, nên "chọn đơn vị" không phải một câu
hỏi. Khi mở ra cho đối tác nhỏ lẻ bên ngoài, người dùng có ngân sách và có
quyền chọn — và Agent phải chọn thay khi họ không nói gì.

Chọn thay là một quyết định có hệ quả tiền bạc. Nên nó phải là MÃ TẤT ĐỊNH,
không phải model:

  * model không bao giờ thấy bảng giá — nó chỉ đọc lại kết quả code tra ra;
  * cùng tham số thì cùng giá, mọi lúc, để hoá đơn đối chiếu được với báo giá;
  * thế cờ hoà phải phá được, nếu không hai lần chạy cho hai kết quả.

Giá THEO THAM SỐ, không phẳng
-----------------------------
Giá phẳng làm "chọn đơn vị rẻ nhất" mất hết ý nghĩa: mọi đơn vị chỉ khác nhau
một hằng số, nên kết quả không phụ thuộc vào thứ người dùng thật sự cần. Ở đây
chuyển nhà tính theo phương tiện + thang máy + bốc xếp, bảo trì tính theo hạng
mục — mỗi đơn vị có hệ số riêng, nên đơn vị rẻ nhất ĐỔI theo yêu cầu.

Lịch trống
----------
Tên gọi
-------
`ten` là tên đầy đủ như trên biển hiệu; `ten_thuong_hieu` là phần RIÊNG của đơn
vị sau khi bỏ mô tả loại hình ("Chuyển nhà", "Vận tải", "Điện lạnh"…).

Tách ra là để `provider_resolver` khớp CHÍNH XÁC được cả hai, mà không phải
dùng phép chứa-nhau. Phép ấy biến "chuyển nhà" — một mô tả loại hình, không
phải một cái tên — thành `MOV-01`, tức biến một lượt trích nhầm của model thành
một lựa chọn tài chính hợp lệ.

Đây là thuộc tính của đơn vị trong CÙNG nguồn canonical, không phải một bảng
tên/alias thứ hai.

`nghi_thu` là ngày trong tuần đơn vị không nhận việc (0 = thứ Hai). Đây là
quy tắc TẤT ĐỊNH theo ngày, nên bài kiểm không phải dựng trạng thái, và một
lượt demo hai lần cho cùng kết quả.

Đây là dữ liệu MOCK. Đơn vị thật sẽ trả giá qua API của họ, và luật ở
`chon_don_vi()` giữ nguyên.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

PhuongTien = Literal["none", "van", "truck"]
HangMuc = Literal["air_conditioning", "electrical", "plumbing", "other"]


@dataclass(frozen=True)
class DonViChuyenNha:
    provider_id: str
    ten: str
    # Tên THƯƠNG HIỆU — phần riêng của đơn vị, đã bỏ mô tả loại hình.
    #
    # Bắt buộc, không có mặc định: một đơn vị mới thêm mà quên khai sẽ vỡ ngay
    # lúc dựng danh mục, chứ không lặng lẽ trở thành một đơn vị khách gọi tên
    # ngắn thì không ai tra ra.
    ten_thuong_hieu: str
    danh_gia: float
    gia_goc: int
    # Phụ phí theo phương tiện. `none` = khách tự lo xe.
    phu_phi_xe: dict[str, int]
    phu_phi_thang_may: int
    phu_phi_boc_xep: int
    nghi_thu: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True)
class DonViBaoTri:
    provider_id: str
    ten: str
    ten_thuong_hieu: str
    danh_gia: float
    gia_goc: int
    phu_phi_hang_muc: dict[str, int]
    nghi_thu: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True)
class DonViDuocChon:
    """Một dòng kết quả tra cứu. `gia` đã tính xong cho ĐÚNG yêu cầu đang hỏi."""

    provider_id: str
    ten: str
    gia: int
    danh_gia: float


# ---------------------------------------------------------------------------
# Danh mục
# ---------------------------------------------------------------------------
# Ba đơn vị mỗi dịch vụ là tối thiểu: hai thì "chọn hợp lý nhất" chỉ là so sánh
# đôi, và không lộ ra được lỗi ở luật phá thế hoà.
#
# Hệ số cố ý ĐAN CHÉO nhau — đơn vị rẻ nhất cho yêu cầu đơn giản không phải đơn
# vị rẻ nhất khi cần xe tải và bốc xếp. Nếu xếp hạng không đổi theo tham số thì
# việc tính giá theo tham số là trang trí.
DON_VI_CHUYEN_NHA: tuple[DonViChuyenNha, ...] = (
    DonViChuyenNha(
        provider_id="MOV-01",
        ten="Chuyển nhà Minh Phát",
        ten_thuong_hieu="Minh Phát",
        danh_gia=4.6,
        gia_goc=250_000,
        phu_phi_xe={"none": 0, "van": 180_000, "truck": 320_000},
        phu_phi_thang_may=60_000,
        phu_phi_boc_xep=150_000,
        nghi_thu=frozenset({6}),  # nghỉ Chủ nhật
    ),
    DonViChuyenNha(
        provider_id="MOV-02",
        ten="Vận tải Đại Tín",
        ten_thuong_hieu="Đại Tín",
        danh_gia=4.8,
        gia_goc=320_000,
        phu_phi_xe={"none": 0, "van": 150_000, "truck": 240_000},
        phu_phi_thang_may=40_000,
        phu_phi_boc_xep=120_000,
        nghi_thu=frozenset(),
    ),
    DonViChuyenNha(
        provider_id="MOV-03",
        ten="Dịch vụ An Khang",
        ten_thuong_hieu="An Khang",
        danh_gia=4.3,
        gia_goc=200_000,
        phu_phi_xe={"none": 0, "van": 220_000, "truck": 400_000},
        phu_phi_thang_may=80_000,
        phu_phi_boc_xep=200_000,
        nghi_thu=frozenset({0}),  # nghỉ thứ Hai
    ),
)

DON_VI_BAO_TRI: tuple[DonViBaoTri, ...] = (
    DonViBaoTri(
        provider_id="FIX-01",
        ten="Kỹ thuật Thành Đạt",
        ten_thuong_hieu="Thành Đạt",
        danh_gia=4.7,
        gia_goc=150_000,
        phu_phi_hang_muc={"air_conditioning": 200_000, "electrical": 120_000, "plumbing": 140_000, "other": 80_000},
        nghi_thu=frozenset(),
    ),
    DonViBaoTri(
        provider_id="FIX-02",
        ten="Sửa chữa Hoà Bình",
        ten_thuong_hieu="Hoà Bình",
        danh_gia=4.4,
        gia_goc=120_000,
        phu_phi_hang_muc={"air_conditioning": 260_000, "electrical": 90_000, "plumbing": 110_000, "other": 100_000},
        nghi_thu=frozenset({6}),
    ),
    DonViBaoTri(
        provider_id="FIX-03",
        ten="Điện lạnh Bách Khoa",
        ten_thuong_hieu="Bách Khoa",
        danh_gia=4.9,
        gia_goc=180_000,
        phu_phi_hang_muc={"air_conditioning": 150_000, "electrical": 200_000, "plumbing": 240_000, "other": 160_000},
        nghi_thu=frozenset({5, 6}),  # nghỉ cuối tuần
    ),
)


# ---------------------------------------------------------------------------
# Giá
# ---------------------------------------------------------------------------
def gia_chuyen_nha(
    don_vi: DonViChuyenNha,
    *,
    move_vehicle: PhuongTien,
    needs_elevator: bool,
    needs_loading_support: bool,
) -> int:
    """Giá cho ĐÚNG yêu cầu này. Thuần, không đọc đồng hồ, không đọc trạng thái."""
    gia = don_vi.gia_goc + don_vi.phu_phi_xe.get(move_vehicle, 0)
    if needs_elevator:
        gia += don_vi.phu_phi_thang_may
    if needs_loading_support:
        gia += don_vi.phu_phi_boc_xep
    return gia


def gia_bao_tri(don_vi: DonViBaoTri, *, issue_type: HangMuc) -> int:
    return don_vi.gia_goc + don_vi.phu_phi_hang_muc.get(issue_type, 0)


# ---------------------------------------------------------------------------
# Lịch trống — NGÀY là ràng buộc CỨNG
# ---------------------------------------------------------------------------
def con_lich(don_vi: DonViChuyenNha | DonViBaoTri, ngay: date) -> bool:
    """Đơn vị có nhận việc ngày này không. Không bao giờ tự đổi ngày để có giá tốt hơn."""
    return ngay.weekday() not in don_vi.nghi_thu


# ---------------------------------------------------------------------------
# Tra cứu
# ---------------------------------------------------------------------------
def _loc(ds: list[DonViDuocChon], max_price: int | None) -> list[DonViDuocChon]:
    """Lọc theo ngân sách. Không ai vừa túi thì trả RỖNG — không nới ngân sách hộ.

    Trả rỗng là câu trả lời đúng và đầy đủ: tầng trên có nhiệm vụ nói ra giá
    thật rẻ nhất và gợi ý ngày khác, chứ không phải nhận về một đơn vị vượt
    ngân sách rồi im lặng đặt nó.
    """
    if max_price is None:
        return ds
    return [x for x in ds if x.gia <= max_price]


def tim_don_vi_chuyen_nha(
    *,
    ngay: date,
    move_vehicle: PhuongTien,
    needs_elevator: bool,
    needs_loading_support: bool,
    max_price: int | None = None,
) -> list[DonViDuocChon]:
    ket_qua = [
        DonViDuocChon(
            provider_id=d.provider_id,
            ten=d.ten,
            gia=gia_chuyen_nha(
                d,
                move_vehicle=move_vehicle,
                needs_elevator=needs_elevator,
                needs_loading_support=needs_loading_support,
            ),
            danh_gia=d.danh_gia,
        )
        for d in DON_VI_CHUYEN_NHA
        if con_lich(d, ngay)
    ]
    return _loc(ket_qua, max_price)


def tim_don_vi_bao_tri(
    *,
    ngay: date,
    issue_type: HangMuc,
    max_price: int | None = None,
) -> list[DonViDuocChon]:
    ket_qua = [
        DonViDuocChon(
            provider_id=d.provider_id,
            ten=d.ten,
            gia=gia_bao_tri(d, issue_type=issue_type),
            danh_gia=d.danh_gia,
        )
        for d in DON_VI_BAO_TRI
        if con_lich(d, ngay)
    ]
    return _loc(ket_qua, max_price)


# ---------------------------------------------------------------------------
# Luật chọn — MỘT hàm duy nhất cho cả ba đường vào
# ---------------------------------------------------------------------------
def chon_don_vi(ung_vien: list[DonViDuocChon]) -> DonViDuocChon:
    """Giá thấp nhất → đánh giá cao hơn → `provider_id` nhỏ hơn.

    Vế cuối trông thừa nhưng không thừa: thiếu nó thì hai đơn vị bằng giá bằng
    đánh giá cho kết quả phụ thuộc thứ tự trong danh mục, và mọi bài kiểm đi
    qua nó đều nhấp nháy.

    Ba đường vào — chọn trên biểu mẫu, nói ngân sách, hoặc không nói gì — đều
    gọi ĐÚNG hàm này. Viết ba nhánh là cách nhanh nhất để chúng lệch nhau.
    """
    if not ung_vien:
        raise ValueError("không có ứng viên nào để chọn")
    return min(ung_vien, key=lambda x: (x.gia, -x.danh_gia, x.provider_id))
