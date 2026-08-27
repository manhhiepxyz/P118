"""Hai ngày trong một câu phải về hai ô khác nhau, không phải cùng một ngày.

Owner: Thành Bảo (Decision layer)
File: tests/test_two_dates_in_one_sentence_go_to_two_fields.py

`_extract_date` dùng `re.search` — **chỉ khớp lần đầu tiên**. Nên khi câu hỏi
gộp hai ô ngày (`viewing_date` + `booking_date`, đúng luồng "tham quan và chỗ
đỗ xe"), MỌI ô ngày đều nhận ngày ĐẦU TIÊN trong câu; ngày thứ hai người dùng
gõ không bao giờ được đọc tới.

Hai hậu quả, và hậu quả thứ hai mới là nghiêm trọng:

    Bạn:  "...ngày 18/8/2026 ... ngày 29/8/2026, khu A"     (18/8 đã qua)
    →     viewing_date = None   (đúng: ngày đã qua)
          booking_date = None   ← SAI: nó nhận 18/8 chứ không phải 29/8,
                                  nên ngày hợp lệ người dùng gõ bị vứt đi

    Bạn:  "...ngày 27/8/2026 ... ngày 29/8/2026..."         (cả hai hợp lệ)
    →     viewing_date = 2026-08-27
          booking_date = 2026-08-27   ← SAI VÀ IM LẶNG

Ca thứ hai không dừng ở màn hình: chỗ đỗ xe được giữ SAI NGÀY, phí vẫn tính,
và không có gì báo cho người dùng biết. Đây là lỗi ĐÚNG DỮ LIỆU, không phải
lỗi trải nghiệm.

Luật thay thế: các ô ngày nhận ngày theo ĐÚNG THỨ TỰ xuất hiện trong câu —
ngày thứ i về ô ngày thứ i. Đây là cách đọc tự nhiên và cũng là thứ tự chính
câu hỏi vừa liệt kê ra. Mỗi ngày vẫn đi qua bộ đọc của riêng ô đó, nên chính
sách lịch (không quá khứ, không quá xa) giữ nguyên.
"""

from __future__ import annotations

from datetime import date, timedelta

from src.api.routes import _extract_follow_up_answers

MAI = (date.today() + timedelta(days=1)).isoformat()
TUAN_SAU = (date.today() + timedelta(days=7)).isoformat()
DA_QUA = (date.today() - timedelta(days=8)).isoformat()


def _vn(iso: str) -> str:
    """`2026-08-29` → `29/8/2026`, đúng cách người dùng gõ."""
    y, m, d = iso.split("-")
    return f"{int(d)}/{int(m)}/{y}"


def test_two_valid_dates_land_on_two_different_fields():
    """Ca NGHIÊM TRỌNG: trước khi sửa, chỗ đỗ xe bị giữ nhầm ngày tham quan."""
    cau = f"Vinhomes Ocean Park, ngày {_vn(MAI)}, lúc 12:00, biển số 19L-87283, ngày {_vn(TUAN_SAU)}, khu A"
    answers, _ = _extract_follow_up_answers(cau, ["viewing_date", "booking_date"])

    assert answers.get("viewing_date") == MAI
    assert answers.get("booking_date") == TUAN_SAU, (
        "ngày thứ hai người dùng gõ phải về ô thứ hai — không thì chỗ đỗ giữ sai ngày mà không ai biết"
    )


def test_the_second_date_is_read_even_when_the_first_one_is_rejected():
    """Ca người dùng gặp: ngày tham quan đã qua, nhưng ngày đặt chỗ vẫn hợp lệ."""
    cau = f"Vinhomes Ocean Park, ngày {_vn(DA_QUA)}, lúc 12:00, ngày {_vn(TUAN_SAU)}, khu A"
    answers, unresolved = _extract_follow_up_answers(cau, ["viewing_date", "booking_date"])

    assert answers.get("booking_date") == TUAN_SAU, "ngày đặt chỗ hợp lệ không được bị ngày tham quan quá khứ nuốt mất"
    assert "viewing_date" in unresolved, "ngày đã qua vẫn phải hỏi lại"


def test_one_date_for_one_field_is_unchanged():
    """Đối chứng: một ô ngày, một ngày — hành vi cũ giữ nguyên."""
    answers, _ = _extract_follow_up_answers(f"ngày {_vn(TUAN_SAU)}", ["viewing_date"])
    assert answers.get("viewing_date") == TUAN_SAU


def test_one_date_for_two_date_fields_only_fills_the_first():
    """Ít ngày hơn số ô → ô sau để trống, KHÔNG nhân bản ngày đầu.

    Nhân bản chính là bug đang sửa. Thà hỏi thêm một câu còn hơn giữ chỗ đỗ
    vào một ngày người dùng chưa từng nói.
    """
    answers, unresolved = _extract_follow_up_answers(f"ngày {_vn(TUAN_SAU)}", ["viewing_date", "booking_date"])
    assert answers.get("viewing_date") == TUAN_SAU
    assert answers.get("booking_date") is None
    assert "booking_date" in unresolved


def test_iso_format_dates_are_matched_in_order_too():
    """Người dùng gõ YYYY-MM-DD cũng phải theo đúng thứ tự."""
    cau = f"tham quan {MAI} rồi đỗ xe {TUAN_SAU} khu B"
    answers, _ = _extract_follow_up_answers(cau, ["viewing_date", "booking_date"])
    assert answers.get("viewing_date") == MAI
    assert answers.get("booking_date") == TUAN_SAU


def test_non_date_fields_in_the_same_sentence_are_untouched():
    """Sửa ô ngày không được làm hỏng các ô khác đọc từ cùng câu."""
    cau = f"Vinhomes Ocean Park, ngày {_vn(MAI)}, lúc 12:00, biển số 19L-87283, xe máy, ngày {_vn(TUAN_SAU)}, khu A"
    answers, _ = _extract_follow_up_answers(
        cau,
        [
            "project_name",
            "viewing_date",
            "viewing_time",
            "plate_number",
            "vehicle_type",
            "booking_date",
            "parking_zone",
        ],
    )
    assert answers.get("project_name") == "Vinhomes Ocean Park"
    assert answers.get("viewing_time") == "12:00"
    assert answers.get("plate_number") == "19L-87283"
    assert answers.get("vehicle_type") == "motorcycle"
    assert answers.get("parking_zone") == "ZONE_A"
    assert answers.get("viewing_date") == MAI
    assert answers.get("booking_date") == TUAN_SAU
