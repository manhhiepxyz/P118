"""Mọi bước chờ duyệt phải nêu tên đơn vị chịu trách nhiệm — không có ngoại lệ.

Cổng duyệt fail-closed: `service_provider_id IS NULL` trả False cho MỌI đơn vị.
Nên một dòng không chủ không phải "ai cũng duyệt được" — nó là "không ai duyệt
được", và nó chết LẶNG LẼ: khách chờ, hàng đợi của mọi provider đều rỗng, không
log nào bất thường.

Hai luật khoá ở đây, cả hai đều thuần và kiểm được không cần database:

  1. MỌI tool có cổng duyệt đều có đơn vị mặc định. Thêm một dịch vụ mà quên
     khai đơn vị thì phải vỡ ở dòng đầu tiên, không phải ở một hàng đợi rỗng
     ba ngày sau.
  2. Tool lạ thì NÉM, không rơi về `LEGACY-DEFAULT`. `LEGACY-DEFAULT` là danh
     tính của dữ liệu có TRƯỚC cột đơn vị, do script backfill gán một lần.
     Một dòng mới rơi vào đó nghĩa là bảng ánh xạ thiếu tool — và nuốt lỗi ấy
     biến một sai sót cấu hình thành một quyền sở hữu sai trông như thật.
"""

from __future__ import annotations

import pytest

from src.orchestration.provider_directory import (
    DON_VI_LEGACY,
    DON_VI_MAC_DINH,
    don_vi_mac_dinh,
    ten_don_vi,
)
from src.orchestration.service_approval import SERVICE_GATED_TOOLS


def test_every_gated_tool_names_a_unit():
    thieu = sorted(t for t in SERVICE_GATED_TOOLS if t not in DON_VI_MAC_DINH)
    assert not thieu, f"tool có cổng duyệt nhưng chưa khai đơn vị: {thieu}"


def test_the_viewing_gate_names_a_unit_too():
    """Lịch tham quan có cổng RIÊNG nên không nằm trong `SERVICE_GATED_TOOLS`.

    Nó vẫn ghi vào cùng bảng `service_approvals`, nên nó vẫn cần chủ sở hữu —
    và vì nó đi đường khác, nó là đúng thứ dễ bị quên.
    """
    assert don_vi_mac_dinh("schedule_property_viewing")


def test_an_unmapped_tool_raises_instead_of_becoming_legacy():
    with pytest.raises(KeyError):
        don_vi_mac_dinh("mot_dich_vu_chua_tung_co")


def test_no_gated_tool_defaults_to_the_legacy_identity():
    """`LEGACY-DEFAULT` không bao giờ là chủ của một dòng MỚI."""
    roi_vao_legacy = sorted(t for t, d in DON_VI_MAC_DINH.items() if d == DON_VI_LEGACY)
    assert not roi_vao_legacy, f"tool gán nhầm danh tính legacy: {roi_vao_legacy}"


def test_every_unit_has_a_name_a_person_can_read():
    """Mã `FIX-01` không nói cho admin biết phải gọi ai."""
    for tool, ma in DON_VI_MAC_DINH.items():
        ten = ten_don_vi(ma)
        assert ten and ten != ma, f"{tool} → {ma} chưa có tên người đọc được"


def test_an_absent_unit_stays_absent():
    """`None` vào thì `None` ra — không dựng ra một cái tên cho một dòng vô chủ."""
    assert ten_don_vi(None) is None
    assert ten_don_vi("") is None
