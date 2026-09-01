"""`DICH_VU_CO_BAO_GIA` có ĐÚNG một phần tử, và nó là `schedule_move`.

Vì sao một bài kiểm cho một dòng hằng số
----------------------------------------
Tập này là CÔNG TẮC PHẠM VI của cả tính năng chọn đơn vị theo báo giá. Thêm một
chuỗi vào đây là bật đường báo giá → đề xuất → khách xác nhận → chọn lại cho một
dịch vụ, và không dòng mã nào khác phải đổi. Một thay đổi rẻ như vậy sẽ được làm
mà không ai bàn.

Hậu quả thì không rẻ. `dich_vu_di_qua_bao_gia()` đọc tập này để quyết định một
bước có DỪNG LẠI hỏi khách hay không. Với một dịch vụ chưa có endpoint báo giá,
mọi yêu cầu rơi vào nhánh "không đơn vị nào báo giá" — tức là hỏng một luồng
đang chạy để mở một luồng chưa chạy.

Bảo trì là ứng viên gần nhất và cũng là cái bẫy rõ nhất: nó đã có ba đơn vị
trong danh mục, có bảng giá, có hàm tính giá. Thiếu đúng một thứ — endpoint ở
mock provider. Nhìn vào `DON_VI_BAO_TRI` thì nó trông đã sẵn sàng.

Bài này KHÔNG cấm mở rộng. Nó bắt việc mở rộng phải đi kèm một lượt sửa bài kiểm
có chủ ý, để người sửa đọc được đoạn văn này trước khi đổi.
"""

from __future__ import annotations

import pytest

from src.orchestration.provider_matching import DICH_VU_CO_BAO_GIA, dich_vu_di_qua_bao_gia
from src.orchestration.service_approval import SERVICE_GATED_TOOLS

CO_BAO_GIA = "schedule_move"

# Bảy dịch vụ legacy — mọi thứ đi qua cổng duyệt mà KHÔNG đi qua báo giá.
# `schedule_property_viewing` có cổng duyệt riêng nên không nằm trong
# `SERVICE_GATED_TOOLS`; nó vẫn phải nằm ngoài đường báo giá.
BAY_DICH_VU_CU = (
    "schedule_property_viewing",
    "register_property_interest",
    "register_vehicle",
    "book_parking",
    "change_parking_zone",
    "book_shuttle",
    "create_maintenance_request",
)


def test_the_set_has_exactly_one_member():
    assert DICH_VU_CO_BAO_GIA == frozenset({CO_BAO_GIA}), DICH_VU_CO_BAO_GIA


@pytest.mark.parametrize("tool", BAY_DICH_VU_CU)
def test_no_legacy_service_is_in_the_set(tool):
    assert tool not in DICH_VU_CO_BAO_GIA


@pytest.mark.parametrize("tool", BAY_DICH_VU_CU)
def test_no_legacy_service_takes_the_quote_path_even_with_the_flag_on(monkeypatch, tool):
    """Cờ BẬT cũng không mở đường cho bảy dịch vụ kia.

    Hai điều kiện, và bài này đo điều kiện THỨ HAI. Đo với cờ tắt thì mọi tool
    đều trả False vì cờ, nên nó không nói gì về tập.
    """
    monkeypatch.setenv("SERVICE_PROVIDER_MATCHING", "1")
    assert dich_vu_di_qua_bao_gia(tool) is False


def test_the_one_service_does_take_it_when_the_flag_is_on(monkeypatch):
    """Nửa còn lại: bài trên phải đỏ được, chứ không phải luôn xanh."""
    monkeypatch.setenv("SERVICE_PROVIDER_MATCHING", "1")
    assert dich_vu_di_qua_bao_gia(CO_BAO_GIA) is True


def test_the_flag_still_wins_over_the_set(monkeypatch):
    """Cờ đứng TRƯỚC tập: tắt cờ thì cả `schedule_move` cũng đi đường cũ."""
    monkeypatch.setenv("SERVICE_PROVIDER_MATCHING", "0")
    assert dich_vu_di_qua_bao_gia(CO_BAO_GIA) is False


def test_the_gated_tools_and_the_quote_set_do_not_drift():
    """Mọi dịch vụ có báo giá phải là một dịch vụ có cổng duyệt.

    Một dịch vụ đi qua báo giá mà không có cổng duyệt nghĩa là khách chọn xong
    đơn vị rồi không ai được hỏi — đề xuất trở thành một lời hứa không ai nhận.
    """
    assert DICH_VU_CO_BAO_GIA <= SERVICE_GATED_TOOLS, DICH_VU_CO_BAO_GIA - SERVICE_GATED_TOOLS
