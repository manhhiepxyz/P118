"""Đơn vị cung cấp nào chịu trách nhiệm một bước dịch vụ.

Vì sao phải có
--------------
`service_approvals.service_provider_id` là chủ sở hữu của một dòng chờ duyệt.
Sau khi cổng duyệt kiểm quyền sở hữu (fail-closed), một dòng KHÔNG có đơn vị là
một dòng không ai quyết định được — nó nằm trong hàng đợi vĩnh viễn mà không
provider nào nhìn thấy.

Nên mọi đường GHI phải gán một đơn vị CỤ THỂ ngay lúc ghim hàng đợi. Ba đường
ấy là `save_pending_service_approvals`, `save_service_request` và
`viewing_approval`; cả ba gọi vào đây, vì ba bảng ánh xạ song song là ba chỗ để
lệch nhau.

Vì sao là bảng cứng, chưa phải lựa chọn
---------------------------------------
Bước A chỉ làm QUYỀN SỞ HỮU. Việc chọn đơn vị theo giá/ngân sách/lịch trống là
bước B, và nó sẽ thay thế đúng hàm `don_vi_mac_dinh()` này chứ không thêm một
đường thứ hai — `chon_don_vi()` ở `src.mock.service_providers` đã là luật chọn
duy nhất, hàm này chỉ là giá trị mặc định khi chưa ai chọn.

`LEGACY-DEFAULT` KHÔNG có trong bảng này. Nó là danh tính dành riêng cho dữ
liệu có trước khi cột tồn tại, do script backfill gán một lần
(`scripts/backfill_service_provider.py`). Một dòng MỚI rơi vào `LEGACY-DEFAULT`
nghĩa là bảng dưới đây thiếu tool — và im lặng nuốt nó sẽ biến một lỗi cấu hình
thành một quyền sở hữu sai.
"""

from __future__ import annotations

# Ánh xạ tool → đơn vị mặc định.
#
# Ba dịch vụ bãi xe dùng chung một đơn vị vì ngoài đời chúng cũng là một: cùng
# ban quản lý bãi, cùng người mở barrier. Tách ra chỉ tạo ba hàng đợi cho một
# đội duy nhất phải mở ba lần.
DON_VI_MAC_DINH: dict[str, str] = {
    "register_vehicle": "BQL-PARK",
    "book_parking": "BQL-PARK",
    "change_parking_zone": "BQL-PARK",
    "create_maintenance_request": "FIX-01",
    "schedule_move": "MOV-01",
    "book_shuttle": "BQL-SHUTTLE",
    "register_property_interest": "BQL-SALES",
    "schedule_property_viewing": "BQL-SALES",
}

# Danh tính của dữ liệu có TRƯỚC cột `service_provider_id`. Không bao giờ được
# gán cho một dòng mới — xem docstring đầu file.
DON_VI_LEGACY = "LEGACY-DEFAULT"
TEN_DON_VI_LEGACY = "P-118 Legacy Provider"


def don_vi_mac_dinh(tool: str) -> str:
    """Đơn vị chịu trách nhiệm `tool`. Ném lỗi nếu chưa khai.

    Ném chứ không trả `None`: một dòng chờ duyệt không chủ sở hữu là một dòng
    chết, và nó chết LẶNG LẼ — khách chờ, hàng đợi rỗng, không log nào bất
    thường. Vỡ ngay lúc ghim là cách duy nhất để lỗi ấy được nhìn thấy.

    Nếu bạn vừa thêm một tool vào `SERVICE_GATED_TOOLS` và gặp lỗi này: đó
    chính là câu hỏi bạn phải trả lời — ai bên kia sẽ bấm Duyệt?
    """
    try:
        return DON_VI_MAC_DINH[tool]
    except KeyError:
        raise KeyError(f"tool {tool!r} chưa khai đơn vị cung cấp mặc định trong DON_VI_MAC_DINH") from None


# Tên người đọc được, cho MÀN GIÁM SÁT. Một mã như `FIX-01` không nói cho admin
# biết phải gọi ai.
_TEN_BQL: dict[str, str] = {
    "BQL-PARK": "Ban quản lý — Bãi xe",
    "BQL-SHUTTLE": "Ban quản lý — Xe đưa đón",
    "BQL-SALES": "Ban quản lý — Kinh doanh",
    DON_VI_LEGACY: TEN_DON_VI_LEGACY,
}


def ten_don_vi(provider_id: str | None) -> str | None:
    """Tên đơn vị để hiển thị. `None` vào thì `None` ra.

    Đơn vị đối tác (MOV-*, FIX-*) lấy tên từ danh mục ở `src.mock.service_providers`
    — một nguồn, không phải hai bảng tên song song. Mã lạ trả về CHÍNH nó chứ
    không phải một chỗ trống: một dòng có chủ mà màn hình vẽ ra trống nhìn y
    hệt một dòng không chủ, và hai thứ ấy cần hai cách xử lý khác nhau.
    """
    if not provider_id:
        return None
    if provider_id in _TEN_BQL:
        return _TEN_BQL[provider_id]
    from src.mock.service_providers import DON_VI_BAO_TRI, DON_VI_CHUYEN_NHA

    for don_vi in (*DON_VI_CHUYEN_NHA, *DON_VI_BAO_TRI):
        if don_vi.provider_id == provider_id:
            return don_vi.ten
    return provider_id
