"""Thông báo lỗi nghiệp vụ dùng chung giữa API và Repair Loop.

Owner: Thành Bảo (Decision layer)
File: src/common/failure_messages.py

Một nguồn DUY NHẤT cho message lỗi nghiệp vụ — API (`_demo_response`),
polling task views và Repair Loop cùng dùng hàm này để hai nơi không bao giờ
lệch nhau. Chỉ nhận mã lỗi đã chuẩn hoá (`ErrorCode`), không bao giờ đưa raw
exception / payload / connection detail ra ngoài.

`task` là object có `.tool` và `.input` (TaskPlan task hoặc row dict). Hàm chỉ
đọc input để lấy thông tin nghiệp vụ đã được allowlist (`apartment_code`,
`plate_number`, `viewing_date`...), không echo dữ liệu không hợp lệ.
"""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> str | None:
    """Chỉ nhận scalar để presentation layer không phát tán raw object."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def task_failure_message(task: Any, title: str, code: str) -> str:
    """Đổi mã lỗi provider thành thông báo nghiệp vụ, không lộ raw exception."""
    inputs = task.input
    if code == "RESIDENT_ALREADY_EXISTS":
        apartment = _text(inputs.get("apartment_code"))
        subject = f"Căn hộ {apartment}" if apartment else "Căn hộ này"
        return f"{subject} đã có hồ sơ cư dân. Hãy sử dụng tài khoản cư dân đã liên kết."
    if code == "VEHICLE_ALREADY_EXISTS":
        plate = _text(inputs.get("plate_number"))
        subject = f"Biển số {plate}" if plate else "Biển số này"
        return f"{subject} đã được đăng ký. Hãy sử dụng phương tiện đã liên kết hoặc kiểm tra lại biển số."
    if code == "NO_AVAILABILITY":
        if task.tool == "schedule_property_viewing":
            viewing_date = _text(inputs.get("viewing_date"))
            viewing_time = _text(inputs.get("viewing_time"))
            slot = " ".join(value for value in (viewing_date, viewing_time) if value)
            suffix = f" {slot}" if slot else " này"
            return f"Khung giờ tham quan{suffix} không còn trống. Hãy chọn thời gian khác."
        booking_date = _text(inputs.get("booking_date"))
        suffix = f" cho ngày {booking_date}" if booking_date else ""
        return f"Khu vực đỗ xe đã hết chỗ{suffix}. Hãy chọn ngày hoặc khu vực khác."
    if code == "BOOKING_ALREADY_EXISTS":
        return "Phương tiện này đã có chỗ đỗ trong ngày được chọn."
    if code == "DEPENDENCY_ERROR":
        return f"Bước “{title}” chưa được thực hiện vì bước trước đó không thành công."
    if code == "INVALID_INPUT":
        return f"Thông tin của bước “{title}” chưa hợp lệ. Hãy kiểm tra lại dữ liệu đã nhập."
    return f"Không thể hoàn thành bước “{title}”. Vui lòng thử lại."
