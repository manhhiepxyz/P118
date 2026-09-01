"""Báo giá phải nói được nó dành cho YÊU CẦU NÀO — và chỉ yêu cầu ấy.

Vân tay là khoá của toàn bộ cơ chế báo giá. Không có nó thì "MOV-02, 620.000đ"
chỉ là một cặp số đi kèm request, và mọi thứ người dùng gửi được thì người dùng
sửa được: xin báo giá cho xe van rồi đặt xe tải với đúng giá ấy.

Ba luật thuần, kiểm được không cần database:

  1. TẤT ĐỊNH — cùng yêu cầu thì cùng vân tay, mọi lúc, kể cả khi ngày tới dưới
     dạng `date` ở lượt này và chuỗi ISO ở lượt sau.
  2. NHẠY VỚI MỌI FIELD ĐỊNH GIÁ — đổi ngày, giờ, xe, thang máy hay bốc xếp đều
     ra vân tay khác. Thiếu một field nghĩa là đổi field ấy mà báo giá cũ vẫn
     dùng được.
  3. MÙ VỚI THỨ KHÔNG ĐỊNH GIÁ — thêm một khoá nội bộ không được vô hiệu hoá
     mọi báo giá đang sống, và `max_price` tuyệt đối không được có mặt.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from src.orchestration.quote import (
    FIELD_CHUYEN_NHA,
    BaoGia,
    loc_theo_ngan_sach,
    payload_gui_provider,
    van_tay_yeu_cau,
)

YEU_CAU = {
    "move_date": "2026-09-30",
    "move_time": "08:00",
    "move_origin_id": "MOVE-Q7-A1",
    "move_destination_id": "MOVE-Q7-B1",
    "move_size": "medium",
    "move_vehicle": "van",
    "needs_elevator": False,
    "needs_loading_support": False,
}


def test_the_same_request_always_fingerprints_the_same():
    assert len({van_tay_yeu_cau(dict(YEU_CAU)) for _ in range(5)}) == 1


def test_a_date_object_and_its_iso_string_are_the_same_request():
    """Cùng một ngày tới từ hai đường vào phải cho cùng một vân tay.

    TaskPlan đã parse trả `date(2026, 9, 30)`; JSONB đọc lại trả chuỗi. Hai
    đường cho hai vân tay nghĩa là báo giá vừa persist xong đã không khớp chính
    yêu cầu sinh ra nó — và nó hỏng ở lượt tiêu thụ, không phải lượt tạo.
    """
    kieu_date = {**YEU_CAU, "move_date": date(2026, 9, 30)}
    assert van_tay_yeu_cau(kieu_date) == van_tay_yeu_cau(dict(YEU_CAU))


@pytest.mark.parametrize(
    ("field", "gia_tri_khac"),
    [
        ("move_date", "2026-10-01"),
        ("move_time", "14:00"),
        ("move_origin_id", "MOVE-Q7-A2"),
        ("move_destination_id", "MOVE-Q7-C1"),
        ("move_size", "large"),
        ("move_vehicle", "truck"),
        ("needs_elevator", True),
        ("needs_loading_support", True),
    ],
)
def test_changing_anything_that_sets_the_price_changes_the_fingerprint(field, gia_tri_khac):
    """Hai cấu hình chuyển nhà khác nhau → vân tay khác nhau. Không có ngoại lệ."""
    khac = {**YEU_CAU, field: gia_tri_khac}
    assert van_tay_yeu_cau(khac) != van_tay_yeu_cau(dict(YEU_CAU)), f"đổi {field} mà vân tay không đổi"


def test_every_pricing_field_is_covered():
    """Bài kiểm trên chỉ mạnh bằng danh sách nó chạy qua.

    Thêm một field định giá vào `FIELD_CHUYEN_NHA` mà quên thêm một ca kiểm thì
    field ấy không được canh — và đó chính xác là cách một field lọt lưới.
    """
    da_kiem = {
        "move_date",
        "move_time",
        "move_origin_id",
        "move_destination_id",
        "move_size",
        "move_vehicle",
        "needs_elevator",
        "needs_loading_support",
    }
    assert set(FIELD_CHUYEN_NHA) == da_kiem, "FIELD_CHUYEN_NHA đổi mà bài kiểm chưa theo"


def test_a_missing_field_is_not_the_same_as_a_false_one():
    """ "Chưa khai `needs_elevator`" và "`needs_elevator=false`" là hai yêu cầu."""
    thieu = {k: v for k, v in YEU_CAU.items() if k != "needs_elevator"}
    assert van_tay_yeu_cau(thieu) != van_tay_yeu_cau(dict(YEU_CAU))


def test_an_internal_key_does_not_invalidate_a_live_quote():
    them = {**YEU_CAU, "_dau_vet_luot_sua": "T1R2", "workflow_id": "abc"}
    assert van_tay_yeu_cau(them) == van_tay_yeu_cau(dict(YEU_CAU))


def test_the_budget_can_never_enter_the_fingerprint():
    """Hai ngân sách khác nhau cho cùng một việc là CÙNG một yêu cầu.

    Ngân sách là thông tin của khách về túi tiền họ, không phải mô tả công việc.
    Cho nó vào vân tay thì mỗi lần khách đổi ý về ngân sách là một lần mọi báo
    giá đang sống bị vô hiệu — và họ phải chờ hỏi lại ba đơn vị.
    """
    assert van_tay_yeu_cau({**YEU_CAU, "max_price": 500_000}) == van_tay_yeu_cau({**YEU_CAU, "max_price": 900_000})
    with pytest.raises(ValueError, match="max_price"):
        van_tay_yeu_cau(YEU_CAU, fields=(*FIELD_CHUYEN_NHA, "max_price"))


def test_the_budget_never_leaves_p118():
    """Payload gửi provider là ALLOWLIST — `max_price` không có đường nào ra.

    Gửi ngân sách đi rồi nhận về một con số sát ngân sách là mời đơn vị định
    giá theo túi tiền người hỏi thay vì theo công việc. Khi ấy "chọn đơn vị rẻ
    nhất" đo một thứ do chính P-118 tạo ra.
    """
    payload = payload_gui_provider({**YEU_CAU, "max_price": 500_000, "ngan_sach": 500_000})
    assert "max_price" not in payload
    assert set(payload) == set(FIELD_CHUYEN_NHA), f"payload mang thêm: {set(payload) - set(FIELD_CHUYEN_NHA)}"


# ---------------------------------------------------------------- lọc, thuần
def _bao_gia(gia: int, *, con_lai_phut: int) -> BaoGia:
    return BaoGia(
        quote_id="q",
        external_quote_id="Q-1",
        service_provider_id="MOV-01",
        service_type="schedule_move",
        amount=gia,
        currency="VND",
        request_fingerprint=van_tay_yeu_cau(dict(YEU_CAU)),
        valid_until=datetime.now(UTC) + timedelta(minutes=con_lai_phut),
        status="ACTIVE",
        workflow_id="w",
        task_id="T1",
    )


def test_an_expired_quote_is_filtered_even_when_it_fits_the_budget():
    """Hàng rào THỨ HAI cho hạn, ở tầng thuần — độc lập với mệnh đề SQL.

    Đường đọc đã lọc `valid_until > NOW()` ở database. Vế này bắt khoảng giữa:
    một báo giá hết hạn SAU lúc đọc và TRƯỚC lúc chọn. Khoảng ấy nhỏ, nhưng nó
    mở đúng lúc hệ thống bận — và nếu bỏ, thứ hiện lên màn hình là một lựa chọn
    không còn tồn tại.

    Kiểm ở đây chứ không qua database là cố ý: nếu bài kiểm đi qua đường đọc
    thì mệnh đề SQL sẽ trả lời hộ, và vế thuần này có bị xoá cũng không ai biết.
    """
    con_han = _bao_gia(470_000, con_lai_phut=30)
    het_han = _bao_gia(100_000, con_lai_phut=-1)

    assert loc_theo_ngan_sach([het_han, con_han], None) == [con_han]
    # Rẻ hơn hẳn ngân sách, nhưng đã chết — không được lọt vào chỉ vì rẻ.
    assert loc_theo_ngan_sach([het_han, con_han], 500_000) == [con_han]
    assert loc_theo_ngan_sach([het_han], 500_000) == []


def test_the_budget_filter_still_filters_by_budget():
    """Vế ngân sách vẫn nguyên — thêm luật hạn không được nuốt luật cũ."""
    re = _bao_gia(420_000, con_lai_phut=30)
    dat = _bao_gia(470_000, con_lai_phut=30)
    assert loc_theo_ngan_sach([re, dat], 425_000) == [re]
    assert loc_theo_ngan_sach([re, dat], None) == [re, dat]
