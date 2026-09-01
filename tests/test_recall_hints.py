from src.api.routes import _flatten_task_inputs, _recalled_hints


def test_hints_come_from_the_most_recent_turn_that_has_the_field():
    """Gợi ý một giá trị từ ba tháng trước trong khi tháng trước họ đã đổi là
    gợi ý SAI — và người dùng gật đầu theo thói quen thì hệ thống vừa đặt lại
    đúng cái họ đã bỏ."""
    recalled = [
        {"ban_da_chon": {"parking_zone": "ZONE_B"}},  # gần nhất
        {"ban_da_chon": {"parking_zone": "ZONE_A"}},  # cũ hơn
    ]
    assert _recalled_hints(recalled, ["parking_zone"]) == {"parking_zone": "ZONE_B"}


def test_only_fields_currently_being_asked():
    recalled = [{"ban_da_chon": {"parking_zone": "ZONE_A", "vehicle_type": "car"}}]
    assert _recalled_hints(recalled, ["parking_zone"]) == {"parking_zone": "ZONE_A"}


def test_dates_are_never_recalled():
    """ "Ngày 01/07" của lần trước gần như chắc chắn không phải ngày lần này."""
    flat = _flatten_task_inputs({"T1": {"parking_zone": "ZONE_A", "booking_date": "2030-07-01"}})
    assert flat == {"parking_zone": "ZONE_A"}


def test_sensitive_values_are_never_recalled():
    """Danh sách ĐÓNG: biển số, số tiền, mã đặt chỗ không đi vào prompt lượt sau."""
    flat = _flatten_task_inputs({"T1": {"plate_number": "51K-11111", "amount": 150000, "booking_id": "BOOK-1"}})
    assert flat == {}


def test_input_refs_are_dropped():
    """InputRef trỏ tới output của task khác trong CÙNG plan cũ — vô nghĩa ở yêu cầu mới."""
    flat = _flatten_task_inputs({"T2": {"parking_zone": {"from_task": "T1", "field": "zone"}}})
    assert flat == {}
